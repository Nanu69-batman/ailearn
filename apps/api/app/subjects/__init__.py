"""Subject (course organisation) model, persistence, and routes.

A *subject* is the unit of organisation users asked for: "create a subject, put
materials in it, and have retrieval scoped so answers only draw on that
subject's materials". It owns no media — it references existing resources by
``resource_id``, so a video/PDF/transcript can belong to several subjects
without being duplicated.

Public surface:
    ``app.subjects.store``  Subject dataclass, JSON-persisted store, and
                            ``resolve_subject_resource_scope`` — the contract
                            the retrieval layer calls.
    ``app.subjects.router`` ``/subjects`` endpoints.
"""