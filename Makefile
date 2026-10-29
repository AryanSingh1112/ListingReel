install:
	pip install -e ".[ui,dev]"
doctor:
	listingreel doctor
test:
	python -m pytest -q
sample:                       ## regenerates the demo photos + the final sample MP4
	listingreel generate samples/property.json --out out
ui:
	streamlit run src/listingreel/app.py
