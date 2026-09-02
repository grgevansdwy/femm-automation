# Legacy Wine/pyfemm backend

Unmodified copies of `engine.py` and `services/femm_service.py` as they were
immediately before the xfemm/femmcli backend migration, kept so the previous
behavior can be restored by copying these two files back over their live
counterparts:

```
cp legacy_wine_backend/engine.py ../engine.py
cp legacy_wine_backend/femm_service.py ../services/femm_service.py
```

Not imported by anything - reference/rollback copies only.
