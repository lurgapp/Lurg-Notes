from app.modules.notes.schemas import NoteBlockIn


def test_notes_v2_block_types_are_accepted_for_incremental_sync():
    for block_type in ("paragraph", "heading1", "heading2", "heading3", "bullet", "numbered", "checklist", "quote", "image", "divider", "code"):
        block = NoteBlockIn(id="00000000-0000-0000-0000-000000000001", position="1000.0000000000", type=block_type)
        assert block.type == block_type
