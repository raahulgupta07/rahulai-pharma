-- Drop orphan ML tables. Writers in `dash/tools/ml_models.py` (_save_model,
-- _save_experiment) have been stubbed; ml_worker source deleted. Nothing
-- reads these tables since the FLAML AutoML chassis was removed.
DROP TABLE IF EXISTS public.dash_ml_experiments CASCADE;
DROP TABLE IF EXISTS public.dash_ml_models CASCADE;
