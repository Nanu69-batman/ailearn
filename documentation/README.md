# Documentation: Malai Pioneer

Technical documentation for Malai Pioneer. Each topic is listed below with the
file that covers it.

| Topic | File |
|---|---|
| Hosted, working application | Web: https://ailearn-theta.vercel.app · API: https://malai-pioneer-api.onrender.com ([Swagger UI](https://malai-pioneer-api.onrender.com/docs)) |
| GitHub source code | The repository root: `apps/api` (FastAPI backend), `apps/web` (Next.js frontend) |
| System architecture diagram | [ARCHITECTURE.md](ARCHITECTURE.md); PNG files in [diagrams/](diagrams/). The RAG pipeline on one page: [9-rag-pipeline.png](diagrams/9-rag-pipeline.png) |
| README and setup documentation | [../README.md](../README.md): quickstart, environment variables, tests |
| API documentation | [API.md](API.md) |
| Sample dataset | [DATASET.md](DATASET.md); the files are in [../test-data/](../test-data/) and the golden set is in [eval/](eval/) |
| Evaluation results and testing details | [EVALUATION.md](EVALUATION.md): 29 questions on our own lecture video and PDF, with the real embedder and AI; raw results in [eval-results/](eval-results/) |
| Deployment instructions | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Live pipeline view (Developer view) | The floating **Developer view** button (in the top bar on small screens); described in [ARCHITECTURE.md](ARCHITECTURE.md#f-developer-view-the-live-pipeline-trace) |
| Accounts, saved chats and file storage (Supabase) | [ARCHITECTURE.md (g)](ARCHITECTURE.md#g-accounts-saved-state-and-file-storage); setup in [DEPLOYMENT.md 1b](DEPLOYMENT.md#1b-supabase-optional-accounts-database-storage); migrations in [../supabase/migrations/](../supabase/migrations/) |
| Voice conversations | [ARCHITECTURE.md (h)](ARCHITECTURE.md#h-voice-conversations); protocol in [API.md](API.md#voice) |
| Future scope (known gaps and planned fixes) | [FUTURE_SCOPE.md](FUTURE_SCOPE.md) |
