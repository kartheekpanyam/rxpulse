-- Migration 003: Document versioning + persistent policy changes

-- Add version tracking to documents
alter table public.documents
  add column if not exists version integer not null default 1,
  add column if not exists previous_version_id uuid references public.documents(id) on delete set null,
  add column if not exists policy_fingerprint text; -- payer + policy_number hash for version detection

-- policy_changes: stores diffs between document versions persistently
create table if not exists public.policy_changes (
    id uuid primary key default gen_random_uuid(),
    payer text not null,
    drug_name text,
    document_id_old uuid references public.documents(id) on delete set null,
    document_id_new uuid references public.documents(id) on delete set null,
    policy_number text,
    change_type text not null, -- criteria_updated | restriction_added | new_coverage | coverage_expanded | coverage_removed
    field_changed text,
    old_value text,
    new_value text,
    impact text,  -- more_restrictive | less_restrictive | neutral
    summary text,
    net_impact text, -- more_restrictive | less_restrictive | mixed | unchanged
    patient_impact_summary text,
    change_date date not null default current_date,
    created_at timestamptz not null default timezone('utc', now())
);

-- Update document_chunks to add richer metadata
alter table public.document_chunks
  add column if not exists payer text,
  add column if not exists drug_name text,
  add column if not exists section_type text, -- prior_auth | step_therapy | indications | site_of_care | general
  add column if not exists page_number integer;

create index if not exists idx_policy_changes_payer on public.policy_changes(payer);
create index if not exists idx_policy_changes_drug on public.policy_changes(drug_name);
create index if not exists idx_policy_changes_date on public.policy_changes(change_date desc);
create index if not exists idx_documents_fingerprint on public.documents(policy_fingerprint);
create index if not exists idx_chunks_payer on public.document_chunks(payer);
create index if not exists idx_chunks_drug on public.document_chunks(drug_name);
create index if not exists idx_chunks_section on public.document_chunks(section_type);
