# Submission deliverables: Malai Pioneer

AHGV BuildVerse 2026, Problem Statement 1. Each required deliverable is listed
below with the file that covers it.

| Requirement | File |
|---|---|
| Hosted, working application | Web: https://ailearn-theta.vercel.app · API: https://malai-pioneer-api.onrender.com ([Swagger UI](https://malai-pioneer-api.onrender.com/docs)) |
| GitHub source code | The repository root: `apps/api` (FastAPI backend), `apps/web` (Next.js frontend) |
| System architecture diagram | [ARCHITECTURE.md](ARCHITECTURE.md); PNG files in [diagrams/](diagrams/). The RAG pipeline on one page: [9-rag-pipeline.png](diagrams/9-rag-pipeline.png) |
| README and setup documentation | [../README.md](../README.md): quickstart, environment variables, tests |
| API documentation | [API.md](API.md) |
| Sample dataset | [DATASET.md](DATASET.md); the files are in [../test-data/](../test-data/) and the golden set is in [eval/](eval/) |
| Evaluation results and testing details | [EVALUATION.md](EVALUATION.md): 29 questions on our own lecture video and PDF, with the real embedder and AI; raw results in [eval-results/](eval-results/) |
| Demo video | [My Video-2.mp4](My%20Video-2.mp4) |
| Deployment instructions | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Live pipeline view (Developer view) | The **Developer view** button in the app's header; described in [ARCHITECTURE.md](ARCHITECTURE.md#f-developer-view-the-live-pipeline-trace) |
| Future scope (known gaps and planned fixes) | [FUTURE_SCOPE.md](FUTURE_SCOPE.md) |
