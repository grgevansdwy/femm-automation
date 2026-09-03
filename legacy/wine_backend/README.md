# Legacy Wine/pyfemm backend

Unmodified copies of `engine.py` and `services/femm_service.py` as they were
immediately before the xfemm/femmcli backend migration, kept so the previous
behavior can be restored by copying these two files back over their live
counterparts:

```
cp legacy/wine_backend/engine.py coil_simulation/engine.py
cp legacy/wine_backend/femm_service.py coil_simulation/services/femm_service.py
```
(paths above are relative to the repo root.)

Not imported by anything - reference/rollback copies only.
