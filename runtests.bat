CALL .venv\Scripts\activate.bat

echo autopep8: & where autopep8 & echo version: & autopep8 --version
autopep8 --in-place --max-line-length 200 -r src

echo pylint: & where pylint & echo version: & pylint --version
pylint --verbose --rcfile=pylintrc src

pause