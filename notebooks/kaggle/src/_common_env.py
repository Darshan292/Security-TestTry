# %% [markdown]
# **Synthetic data only.** Nothing in this notebook reads real people's data, the repo's sealed held-out set or any tester
# set; none of those files are uploaded to Kaggle/Colab. Labels come from code, not from the LLM.

# %% Environment: Kaggle vs Colab vs local
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ON_KAGGLE = Path("/kaggle/working").exists()
ON_COLAB = (not ON_KAGGLE) and importlib.util.find_spec("google.colab") is not None
if ON_KAGGLE:
    INPUT, WORK = Path("/kaggle/input"), Path("/kaggle/working")
elif ON_COLAB:
    from google.colab import drive  # type: ignore[import-not-found]
    drive.mount("/content/drive")  # outputs and checkpoints live on Drive so they survive a disconnect
    INPUT, WORK = Path("/content/drive/MyDrive/pgw_ml/input"), Path("/content/drive/MyDrive/pgw_ml/work")
else:
    INPUT, WORK = Path(os.environ.get("PGW_INPUT", "./input")), Path(os.environ.get("PGW_WORK", "./work"))
INPUT.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)
print("platform:", "kaggle" if ON_KAGGLE else "colab" if ON_COLAB else "local", "| input:", INPUT, "| work:", WORK)


def pip_install(*pkgs):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=True)


def find_files(name, root=None, max_depth=6):
    """All files called `name` under root (default INPUT), shallowest first."""
    root = Path(root or INPUT)
    hits = [p for p in root.rglob(name) if len(p.relative_to(root).parts) <= max_depth]
    return sorted(hits, key=lambda p: len(p.parts))


# %% Install the ml/train package from the attached dataset (the attached dataset is the only source)
import zipfile

pkg_roots = [p.parent.parent for p in find_files("__init__.py") if p.parent.name == "train" and p.parent.parent.name == "ml"]
if pkg_roots:
    PKG = pkg_roots[0]
else:
    zips = find_files("ml_train.zip")
    assert zips, ("ml_train.zip / ml/train not found under " + str(INPUT) + ". Attach the dataset made by "
                  "scripts/ml/package_for_kaggle.py (Kaggle: Add data; Colab: copy ml_train.zip into the input folder).")
    PKG = WORK / "pkg"
    with zipfile.ZipFile(zips[0]) as z:
        z.extractall(PKG)
sys.path.insert(0, str(PKG))
import ml.train  # noqa: E402

print("ml.train", ml.train.__version__, "from", PKG)
