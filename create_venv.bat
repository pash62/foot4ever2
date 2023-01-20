python -m venv .venv
call .venv\scripts\activate.bat
python -m pip install pip setuptools --upgrade
pip install -r requirements.txt
pip install -r requirements-test.txt
pause
