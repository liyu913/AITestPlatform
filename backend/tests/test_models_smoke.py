def test_import_models_registers_tables():
    from app.models import IndexChunk, KnowledgeDocument, Project, ProjectMember  # noqa: F401
    from app.models import Requirement, TestCase, TestCaseStep, User  # noqa: F401
    from app.models.base import Base

    names = {t.name for t in Base.metadata.tables.values()}
    assert "users" in names
    assert "project_members" in names
    assert "index_chunks" in names
