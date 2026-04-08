-- Performance indexes identified by codebase audit

-- Documents: frequently filtered by payer and ordered by created_at
create index if not exists idx_documents_payer on public.documents(payer);
create index if not exists idx_documents_created_at on public.documents(created_at desc);

-- Drug coverages: compound index for search + stats queries
create index if not exists idx_coverages_payer_drug on public.drug_coverages(payer, drug_name);
create index if not exists idx_coverages_status on public.drug_coverages(coverage_status);
create index if not exists idx_coverages_step_therapy on public.drug_coverages(step_therapy);
create index if not exists idx_coverages_prior_auth on public.drug_coverages(prior_authorization);

-- Policy changes: version-to-version lookups
create index if not exists idx_changes_doc_old on public.policy_changes(document_id_old);
create index if not exists idx_changes_doc_new on public.policy_changes(document_id_new);
