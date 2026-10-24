# Optional dependencies

Run these commands from the repository root in the application's virtual environment.
The core installation remains `python -m pip install -r requirements.txt`.

| Purpose | Install command |
| --- | --- |
| Desktop packaging | `python -m pip install -r requirements/build.txt` |
| Tests | `python -m pip install -r requirements/test.txt` |
| Machine learning | `python -m pip install -r requirements/ml.txt` |
| Deep learning | `python -m pip install -r requirements/dl.txt` |

For deep learning, select a PyTorch build matching your platform and CUDA version
before installing the optional dependencies. See the [PyTorch installation guide](https://pytorch.org/get-started/locally/).

Remote connectivity dependencies (`paramiko` and `keyring`) are included in the
root `requirements.txt`; there is no separate remote dependency manifest.
