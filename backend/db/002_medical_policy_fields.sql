alter table public.documents
add column if not exists payer text,
add column if not exists policy_number text,
add column if not exists effective_date date,
add column if not exists last_reviewed_date date;

alter table public.drug_coverages
add column if not exists brand_names jsonb not null default '[]'::jsonb,
add column if not exists hcpcs_code varchar(10),
add column if not exists covered_indications jsonb not null default '[]'::jsonb,
add column if not exists prior_auth_criteria jsonb not null default '[]'::jsonb,
add column if not exists step_therapy_requirements jsonb not null default '[]'::jsonb,
add column if not exists quantity_limit_detail text,
add column if not exists site_of_care jsonb not null default '[]'::jsonb,
add column if not exists prescriber_requirements text,
add column if not exists payer text,
add column if not exists policy_number text,
add column if not exists effective_date date,
add column if not exists last_reviewed_date date;

create index if not exists idx_drug_coverages_hcpcs_code
on public.drug_coverages(hcpcs_code);

create index if not exists idx_drug_coverages_payer
on public.drug_coverages(payer);
