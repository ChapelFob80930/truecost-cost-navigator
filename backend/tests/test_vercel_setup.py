"""The deploy setup (Vercel service, CI, secret scan) must not drift out of step with the app."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def names(path):
    out = set()
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(re.split(r"[\[<>=!~ ]", line)[0].lower())
    return out


# ------------------------------------------------------------------ dependencies
def test_requirements_are_pinned_to_exact_versions():
    lines = [ln.strip() for ln in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines and all("==" in ln for ln in lines), [ln for ln in lines if "==" not in ln]


def test_requirements_cover_everything_the_app_imports_at_run_time():
    need = {"fastapi", "pydantic", "httpx", "google-genai", "psycopg", "pypdf", "python-multipart"}
    assert need <= names("backend/requirements.txt")


def test_no_notebook_packages_are_installed_on_vercel():
    assert not (names("backend/requirements.txt") & {"pandas", "jupyter", "ipykernel", "streamlit"})


# ------------------------------------------------------------------ the Vercel configuration
def test_vercel_json_declares_one_public_backend_service():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert list(cfg["services"]) == ["backend"]
    svc = cfg["services"]["backend"]
    assert svc["root"] == "backend" and svc["framework"] == "fastapi" and svc["entrypoint"] == "index:app"
    assert "bindings" not in svc                                   # nothing calls another service
    assert cfg["rewrites"] == [{"source": "/(.*)", "destination": {"service": "backend"}}]    # the web app calls /estimate, /extract, ... at the root
    assert 30 <= svc["functions"]["index.py"]["maxDuration"] <= 60  # long enough for the AI agents, within the free-plan limit
    for key in ("functions", "regions", "builds", "installCommand", "buildCommand", "framework"):
        assert key not in cfg, f"'{key}' is not valid at the top level when services are used"


def test_the_service_entry_point_exposes_a_fastapi_app():
    src = (BACKEND / "index.py").read_text(encoding="utf-8")
    assert "create_app" in src and re.search(r"^app\s*=\s*create_app\(\)", src, flags=re.M)


def test_vercelignore_keeps_secrets_and_raw_data_out_but_not_the_data_the_app_needs():
    lines = {ln.strip() for ln in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    assert ".env" in lines and "_srcdata/" in lines and "/data/" in lines
    assert "data/" not in lines                          # would also drop backend/data
    assert not ({"backend/", "backend/data/", "backend/app/", "backend/index.py"} & lines)


# ------------------------------------------------------------------ the service must work from its OWN folder
def test_data_paths_are_relative_to_the_app_folder_not_the_repository():
    """A Vercel service is built from backend/ alone, so '<repo>/backend/data' does not exist there."""
    from app import config
    assert config.DATA_DIR == Path(config.__file__).resolve().parents[1] / "data" and config.DATA_DIR.is_dir()
    for py in (BACKEND / "app").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert '"backend" / "data"' not in text, f"{py.name} builds a data path from the repository root"


def test_the_service_runs_from_a_copy_of_only_its_own_folder(tmp_path):
    root = tmp_path / "backend"
    shutil.copytree(BACKEND, root, ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache", "*.pyc", ".env*"))
    probe = (
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "from fastapi.testclient import TestClient\n"
        "from index import app\n"
        "c = TestClient(app)\n"
        "d = c.post('/api/v1/assistance/match', json={'demo_mode': True, 'case': {'household_size': 3, 'annual_income': 52000}})\n"
        "print('DEMO', d.status_code, len(d.json().get('matches', [])))\n"
        "print('HEALTH', c.get('/health').status_code)\n")
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("DATABASE_URL", "GEMINI_API_KEY")}
    env["DB_PATH"] = str(ROOT / "truecost.db")
    p = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert "HEALTH 200" in p.stdout and re.search(r"DEMO 200 [1-9]", p.stdout), p.stdout + p.stderr[-500:]


# ------------------------------------------------------------------ the pipeline files
def test_workflows_wire_ci_before_deploy_and_run_the_scripts_that_exist():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "workflow_call" in ci and "pytest" in ci and "secret_scan.py" in ci and "check_vercel_bundle.py" in ci
    assert "backend/requirements.txt" in ci and "api/requirements.txt" not in ci
    assert "uses: ./.github/workflows/ci.yml" in deploy and "needs: ci" in deploy
    assert "smoke_test.py" in deploy and "vercel deploy --prebuilt" in deploy
    assert "configured=false" in deploy                  # skips cleanly until the Vercel secrets exist
    for script in re.findall(r"scripts/([a-z_]+\.py)", ci + deploy):
        assert (ROOT / "scripts" / script).exists(), script


def test_secrets_are_only_read_from_the_secrets_context():
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert re.search(r"VERCEL_TOKEN:\s*\$\{\{\s*secrets\.VERCEL_TOKEN\s*\}\}", deploy)
    assert "echo $VERCEL_TOKEN" not in deploy and 'echo "$VERCEL_TOKEN"' not in deploy


# ------------------------------------------------------------------ the secret scan
def test_secret_scan_catches_real_looking_secrets_and_ignores_placeholders(tmp_path):
    scan = load_script("secret_scan")
    bad = {
        "google.txt": "key = " + "AIza" + "x" * 35,
        "dsn.txt": "postgres://tsdbadmin:" + "hunter2hunter2" + "@host.example.com:5432/db",
        "pg.env": "PGPASSWORD=" + "s3cretpassw0rd",
        "gh.txt": "token " + "ghp_" + "a" * 36,
        "oauth.txt": "GOCSPX-" + "b" * 28,
        "key.pem": "-----BEGIN " + "PRIVATE KEY-----",
    }
    ok = {
        "example.env": "GEMINI_API_KEY=your-gemini-key\n# DATABASE_URL=postgres://tsdbadmin@HOST.tsdb.cloud.timescale.com:5432/tsdb\nPGPASSWORD=your-tiger-service-password\n",
        "doc.md": "Set VERCEL_TOKEN as a repository secret. postgres://user:password@host/db is only an example.",
    }
    paths = []
    for name, text in {**bad, **ok}.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    problems = scan.scan(paths, root=tmp_path)
    flagged = {line.split(":")[0] for line in problems}
    assert flagged == set(bad), flagged ^ set(bad)
    assert not any("example.env" in p or "doc.md" in p for p in problems)


def test_the_repository_itself_has_no_secrets():
    scan = load_script("secret_scan")
    assert scan.scan(scan.tracked_files()) == []
