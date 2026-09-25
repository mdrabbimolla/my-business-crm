# Central CRM Architecture — Phase 1

The Android APK currently stores SQLite inside each phone's private app storage. Therefore users and CRM records created on one phone are not automatically available on another phone.

This phase adds a central API authentication foundation without changing the existing APK database flow yet.

Components:
- cloud_api.py — central authentication API
- tests/test_cloud_api.py — automated API tests
- requirements-cloud.txt — server dependencies

Security:
- Passwords are stored as Werkzeug password hashes.
- Login returns a random bearer token.
- Bootstrap requires CRM_API_SECRET.
- The API does not expose arbitrary SQL execution.

Next:
The existing Flask routes must be migrated from direct local SQLite access to authenticated central API operations. CRM tables will then be moved to the hosted central database. Only after that should the APK switch to the central API.

A real hosted database/service is required for actual two-phone sharing. Phase 1 does not claim that the existing APK is synchronized.
