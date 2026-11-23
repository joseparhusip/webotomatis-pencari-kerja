"""Target CV contoh untuk tes (data tes, bukan data aplikasi).

Skor & pencocokan skill sekarang mengikuti CV aktif di Profil. Tes lama ditulis untuk CV pengembang
full stack, jadi target contoh berikut dipasang otomatis; tes yang butuh target lain memanggil set_target sendiri.
"""

import pytest

from app.services import targeting

SAMPLE_DEV_SKILLS = [
    "Go", "Next.js", "React", "Vue.js", "TypeScript", "JavaScript", "Tailwind", "Node.js", "PHP", "PostgreSQL",
    "MySQL", "REST API", "Docker", "Git", "Flutter", "Dart", "Firebase", "Python", "JWT/RBAC", "Figma", "Swagger",
]
SAMPLE_DEV_CATEGORIES = ["fullstack", "backend", "frontend", "mobile", "data", "it_other", "qa", "it_support"]


@pytest.fixture(autouse=True)
def sample_dev_target():
    previous = targeting.current_target()
    target = targeting.set_target(SAMPLE_DEV_CATEGORIES, [], SAMPLE_DEV_SKILLS, 1)
    yield target
    targeting._current = previous
