"""The deploy setup (Vercel files, CI, secret scan) must not drift out of step with the app."""
import glob
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
def test_vercel_requirements_are_a_subset_of_the_backend_requirements():
    assert names("api/requirements.txt") <= names("backend/requirements.txt")


def test_vercel_requirements_cover_everything_the_app_imports_at_run_time():
    need = {"fastapi", "pydantic", "httpx", "google-genai", "psycopg", "pypdf", "python-multipart"}
    assert need <= names("api/requirements.txt")


def test_vercel_does_not_install_test_or_notebook_packages():
    assert not (names("api/requirements.txt") & {"pytest", "pandas", "jupyter", "ipykernel", "streamlit", "uvicorn"})


# ------------------------------------------------------------------ what gets shipped
def test_every_runtime_data_file_is_included_in_the_vercel_bundle():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    pat = cfg["functions"]["api/index.py"]["includeFiles"]
    patterns = pat[1:-1].split(",") if pat.startswith("{") else [pat]
    included = {Path(p).as_posix() for g in patterns for p in glob.glob(str(ROOT / g), recursive=True)}
    included = {Path(p).relative_to(ROOT).as_posix() for p in included}
    for need in ("truecost.db", "backend/data/assistance_programs.json", "backend/data/billing_rights.json",
                 "backend/data/fpl_guidelines.json", "backend/data/cached/sarah_assistance.json", "backend/data/cached/sarah_appeal.json"):
        assert need in included, need


def test_vercelignore_keeps_secrets_and_raw_data_out_but_not_the_data_the_app_needs():
    lines = {ln.strip() for ln in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    assert ".env" in lines and "_srcdata/" in lines and "/data/" in lines
    assert "data/" not in lines                          # would also drop backend/data
    assert not ({"backend/", "backend/data/", "*.db", "truecost.db", "backend/app/"} & lines)


def test_vercel_function_settings():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["rewrites"] == [{"source": "/(.*)", "destination": "/api/index"}]
    assert 30 <= cfg["functions"]["api/index.py"]["maxDuration"] <= 60      # long enough for the AI agents, within the free-plan limit
    assert (ROOT / "api" / "index.py").read_text(encoding="utf-8").count("app = create_app()") == 1


# ------------------------------------------------------------------ the pipeline files
def test_workflows_wire_ci_before_deploy_and_run_the_scripts_that_exist():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "workflow_call" in ci and "pytest" in ci and "secret_scan.py" in ci and "check_vercel_bundle.py" in ci
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
