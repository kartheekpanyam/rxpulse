-- Vector similarity search function for RAG
create or replace function match_chunks(
  query_embedding vector(768),
  match_count int default 12
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index int,
  content text,
  payer text,
  drug_name text,
  section_type text,
  page_number int,
  metadata jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    dc.id,
    dc.document_id,
    dc.chunk_index,
    dc.content,
    dc.payer,
    dc.drug_name,
    dc.section_type,
    dc.page_number,
    dc.metadata,
    1 - (dc.embedding <=> query_embedding) as similarity
  from public.document_chunks dc
  where dc.embedding is not null
  order by dc.embedding <=> query_embedding
  limit match_count;
end;
$$;
