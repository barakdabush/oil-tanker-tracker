---
description: Run E2E Playwright tests using docker-compose.test.yml
---

To run the end-to-end tests for the oil tanker monitor:

1. Ensure the test environment is built and active:
// turbo
```bash
docker compose -f docker-compose.test.yml build
```

2. Start the test environment (includes DB, Backend and Frontend):
// turbo
```bash
docker compose -f docker-compose.test.yml up -d
```

3. Wait for all services to be healthy (optional but recommended):
```bash
docker compose -f docker-compose.test.yml ps
```

4. Run the Playwright tests from the frontend directory:
// turbo
```bash
cd frontend && npx playwright test
```

5. After tests finish, shut down the test environment:
// turbo
```bash
docker compose -f docker-compose.test.yml down -v
```

> [!NOTE]
> The database is automatically seeded via `backend/seed_test.sql` on startup. 
> Ensure `docker-compose.test.yml` is correctly configured with `NEXT_PUBLIC_API_URL` pointing to the test backend.
