# TrueCost front end (Streamlit)

```
# terminal 1 - backend (port 8000 may be taken; use any free port)
cd backend && python -m uvicorn app.main:create_app --factory --port 8010

# terminal 2 - UI
cd frontend && TRUECOST_API=http://127.0.0.1:8010 python -m streamlit run app.py
```
Flow: note -> /extract -> confirm codes -> insurance -> /estimate (ranked hospitals + map) -> /simulate (cost curve).
