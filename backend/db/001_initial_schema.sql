create extension if not exists vector;

create table if not exists public.plans (
    id uuid primary key default gen_random_uuid(),
    insurer_name text not null,
    plan_name text not null,
    plan_year integer,
    state text,
    plan_type text,
    source text default 'manual',
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.documents (
    id uuid primary key default gen_random_uuid(),
    plan_id uuid references public.plans(id) on delete cascade,
    file_name text not null,
    file_url text,
    document_type text default 'formulary',
    source_url text,
    raw_text text,
    status text not null default 'pending',
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.drug_coverages (
    id uuid primary key default gen_random_uuid(),
    plan_id uuid not null references public.plans(id) on delete cascade,
    document_id uuid references public.documents(id) on delete set null,
    drug_name text not null,
    drug_tier text,
    prior_authorization boolean default false,
    quantity_limit boolean default false,
    step_therapy boolean default false,
    coverage_status text,
    notes text,
    confidence_score numeric(5,4),
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.document_chunks (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    chunk_index integer not null,
    content text not null,
    embedding vector(768),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default timezone('utc', now()),
    unique (document_id, chunk_index)
);

create index if not exists idx_plans_insurer_name on public.plans(insurer_name);
create index if not exists idx_documents_plan_id on public.documents(plan_id);
create index if not exists idx_drug_coverages_plan_id on public.drug_coverages(plan_id);
create index if not exists idx_drug_coverages_drug_name on public.drug_coverages(drug_name);
create index if not exists idx_document_chunks_document_id on public.document_chunks(document_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = timezone('utc', now());
    return new;
end;
$$;

drop trigger if exists set_plans_updated_at on public.plans;
create trigger set_plans_updated_at
before update on public.plans
for each row
execute function public.set_updated_at();

drop trigger if exists set_documents_updated_at on public.documents;
create trigger set_documents_updated_at
before update on public.documents
for each row
execute function public.set_updated_at();

drop trigger if exists set_drug_coverages_updated_at on public.drug_coverages;
create trigger set_drug_coverages_updated_at
before update on public.drug_coverages
for each row
execute function public.set_updated_at();
