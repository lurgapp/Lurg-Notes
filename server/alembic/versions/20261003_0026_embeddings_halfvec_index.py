"""Index 3072-dimensional embeddings using pgvector's half precision type."""
from alembic import op

revision = "20261003_0026"
down_revision = "20261003_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector HNSW vector has a 2000 dimension ceiling, halfvec supports 4000.
    # Source embeddings stay full precision; only index candidate ordering is
    # quantized. Returned similarity is still computed on original vectors.
    op.execute("CREATE INDEX IF NOT EXISTS embeddings_halfvec_idx ON embeddings USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS embeddings_halfvec_idx")
