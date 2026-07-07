# Vendored calc engines

Copies of the shared NEC calc engines from `../Cable-Optimisation-app`, bundled
so the container image is self-contained (`ENGINE_DIR=/app/engines`).

**Re-sync when the desktop engines change:**
```
cp ../Cable-Optimisation-app/{nec_tables,ac_calculation_engine,calculation_engine}.py engines/
```
