"""Contoh kalimat & bukti surat untuk tes (bukan data aplikasi)."""

from app.services.letter_template import Evidence

SAMPLE_EVIDENCE: list[Evidence] = [
    Evidence(
        "Di PT Sistem Pelayanan Kesehatan dan Data, saya mengembangkan backend platform MedCredix menggunakan "
        "Go (Gin), GORM, dan PostgreSQL, mencakup 44 endpoint REST API yang terdokumentasi dengan Swagger, "
        "autentikasi JWT dengan kontrol akses berbasis peran (RBAC), serta deployment menggunakan Docker Compose.",
        "At PT Sistem Pelayanan Kesehatan dan Data, I develop the backend of the MedCredix platform using Go (Gin), "
        "GORM and PostgreSQL, covering 44 REST API endpoints documented with Swagger, JWT authentication with "
        "role-based access control (RBAC), and deployment with Docker Compose.",
        frozenset({"backend", "fullstack"}),
        frozenset({"Go", "PostgreSQL", "Docker", "REST API", "JWT/RBAC", "Swagger"}),
    ),
    Evidence(
        "Untuk sisi frontend, saya mengembangkan dashboard Sistem Informasi Manajemen Rumah Sakit (SIMRS) di "
        "PT Sistem Pelayanan Kesehatan dan Data menggunakan Next.js, React, TypeScript, dan Tailwind CSS, yang "
        "terintegrasi dengan REST API berdasarkan kontrak Swagger serta validasi data menggunakan Zod.",
        "On the frontend side, I develop a hospital information system (SIMRS) dashboard at PT Sistem Pelayanan "
        "Kesehatan dan Data using Next.js, React, TypeScript and Tailwind CSS, integrated with a REST API based on "
        "a Swagger contract and validated with Zod.",
        frozenset({"frontend", "fullstack"}),
        frozenset({"Next.js", "React", "TypeScript", "Tailwind", "JavaScript", "Swagger"}),
    ),
    Evidence(
        "Sebagai Mobile Developer di JEC Eye Hospitals & Clinics, saya membangun aplikasi Android menggunakan "
        "Flutter beserta backend Node.js/Express, termasuk fitur OCR KTP, push notification FCM, dan Google "
        "Sign-In; uji usability terhadap 120 responden menghasilkan skor SUS 79 (kategori baik).",
        "As a Mobile Developer at JEC Eye Hospitals & Clinics, I built an Android app with Flutter and its "
        "Node.js/Express backend, including ID-card OCR, FCM push notifications and Google Sign-In; a usability "
        "test with 120 respondents scored 79 on the System Usability Scale.",
        frozenset({"mobile", "fullstack"}),
        frozenset({"Flutter", "Dart", "Firebase", "Node.js", "JavaScript", "REST API"}),
    ),
    Evidence(
        "Sebagai Back End Developer di Kantor Pusat PT Pos Indonesia, saya membangun backend aplikasi IHSAN POS "
        "dengan PostgreSQL, mulai dari perancangan ERD hingga pengujian seluruh endpoint menggunakan Postman; "
        "hasil proyek tersebut dipublikasikan di Jurnal JUISI.",
        "As a Back End Developer at PT Pos Indonesia headquarters, I built the IHSAN POS backend on PostgreSQL, "
        "from ERD design to testing every endpoint in Postman; the project was published in the JUISI journal.",
        frozenset({"backend"}),
        frozenset({"PostgreSQL", "REST API"}),
    ),
    Evidence(
        "Pada proyek SecuraKost dan Toko Kelontong Online, saya membangun antarmuka web menggunakan Vue.js "
        "(Composition API dan Pinia) serta Tailwind CSS, termasuk pengelolaan state keranjang dan filter produk "
        "secara reaktif.",
        "In the SecuraKost and Toko Kelontong Online projects, I built web interfaces with Vue.js (Composition API "
        "and Pinia) and Tailwind CSS, including reactive cart state and product filters.",
        frozenset({"frontend"}),
        frozenset({"Vue.js", "Tailwind", "JavaScript"}),
    ),
    Evidence(
        "Pada proyek Digimize, saya mengembangkan aplikasi Android secara end-to-end menggunakan Flutter yang "
        "terhubung dengan REST API berbasis PHP dan MySQL.",
        "In the Digimize project, I developed an end-to-end Android app with Flutter connected to a PHP and "
        "MySQL REST API.",
        frozenset({"mobile"}),
        frozenset({"Flutter", "Dart", "PHP", "MySQL", "REST API"}),
    ),
    Evidence(
        "Pada proyek Website Carter ULBI, saya membangun aplikasi web full stack menggunakan PHP dan MySQL, "
        "termasuk integrasi payment gateway Midtrans dan RajaOngkir API, hingga deployment ke server produksi.",
        "In the Carter ULBI website project, I built a full stack web application with PHP and MySQL, including "
        "Midtrans payment gateway and RajaOngkir API integrations, through to production deployment.",
        frozenset({"fullstack", "backend", "frontend"}),
        frozenset({"PHP", "MySQL", "REST API", "JavaScript", "HTML", "CSS", "Bootstrap"}),
    ),
]


SAMPLE_INTRO_ID = ("Saya merupakan lulusan S1 Bisnis Digital dengan IPK 3,75/4,00 yang saat ini bekerja sebagai "
                    "pengembang full stack untuk produk teknologi kesehatan.")
SAMPLE_INTRO_EN = ("I am a Digital Business graduate (GPA 3.75/4.00) currently working as a full stack developer "
                    "on healthcare technology products.")
SAMPLE_STRENGTH_ID = ("Selain kemampuan teknis, saya terbiasa menganalisis kebutuhan dan memodelkan sistem "
                       "menggunakan UML, ERD, dan kontrak API sebelum implementasi, sehingga dapat berkoordinasi "
                       "dengan baik bersama tim produk maupun tim pengembang.")
SAMPLE_STRENGTH_EN = ("Beyond technical skills, I am used to analysing requirements and modelling systems with UML, "
                       "ERD and API contracts before implementation, which helps me work closely with both product "
                       "and engineering teams.")
