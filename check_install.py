"""Run this first:  python check_install.py   It tells you exactly what is missing or out of date."""
import importlib, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
need = ["app.py", "ui.py", "requirements.txt", ".streamlit/config.toml"] + \
       [f"core/{n}.py" for n in "__init__ aba agents authz db finance gold ledger money providers runner secrets security".split()] + \
       [f"views/{n}.py" for n in "command_centre agent_hub vault_dashboard vault_terminal gold_vault accounting projects jobs security_settings".split()]
missing = [f for f in need if not os.path.exists(os.path.join(ROOT, f))]
print("Python", sys.version.split()[0])
bad = False
if missing:
    bad = True
    print("\nMISSING FILES (copy the whole folder again from the zip, do not merge):")
    for f in missing:
        print("  -", f)
for pkg, minv in [("streamlit", "1.40"), ("pandas", "2.0"), ("anthropic", "0.40"), ("requests", "2.31")]:
    try:
        m = importlib.import_module(pkg); v = getattr(m, "__version__", "?")
        ok = tuple(int(x) for x in v.split(".")[:2] if x.isdigit()) >= tuple(int(x) for x in minv.split("."))
        print(f"{pkg} {v}" + ("" if ok else f"   <-- too old, need {minv}+  (run: pip install -U -r requirements.txt)"))
        bad = bad or not ok
    except ImportError:
        print(f"{pkg} NOT INSTALLED   (run: pip install -r requirements.txt)"); bad = True
if not bad:
    sys.path.insert(0, ROOT)
    try:
        from core import aba, authz, db, finance, ledger, providers, security, agents, gold, runner, secrets  # noqa
        print("\nAll good. Start the app with:  streamlit run app.py")
    except Exception as e:
        print("\nImport problem:", repr(e)); bad = True
sys.exit(1 if bad else 0)
