# Width of the pgvector column in `chunk_embeddings`. The database fixes it; a model with another
# dimension needs a migration first, so the switch is deliberate rather than accidental.
VECTOR_DIMENSION = 384
