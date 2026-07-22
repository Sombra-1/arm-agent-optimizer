# Quality policy

The quality gate runs before ranking. It checks absolute request/task/JSON/validator rates, permitted percentage-point regression from the fresh baseline, timeout maximums, completed evidence, repetitions, and critical validator failure counts/rates.

`0.01` regression means one absolute percentage point, not one percent relative. A candidate below an absolute floor and beyond the regression allowance records both violations. Critical validators such as `request_succeeded` and `not_contains_text` cannot gain failures even when the baseline already has a limitation.

Quality failure excludes a profile from final ranking but does not erase its evidence. Policies are strict YAML, hashed, and embedded by reference in the Passport.
