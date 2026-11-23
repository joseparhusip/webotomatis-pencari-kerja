from enum import StrEnum


class RoleCategory(StrEnum):
    FULLSTACK = "fullstack"
    FRONTEND = "frontend"
    BACKEND = "backend"
    MOBILE = "mobile"
    QA = "qa"                  # QA / tester / test engineer
    IT_SUPPORT = "it_support"  # IT support, helpdesk, jaringan, sysadmin, NOC
    DATA = "data"              # data / BI analyst, data engineer, AI/ML
    IT_OTHER = "it_other"      # system analyst, devops, security, UI/UX, IT project, ERP, IT lainnya
    # Non-IT (supaya CV non-IT juga mendapat lowongan yang sesuai bidangnya)
    MARKETING = "marketing"                # pemasaran, digital marketing, social media, brand
    CREATIVE = "creative"                  # desain grafis, video editor, konten kreator, copywriter, fotografer
    SALES = "sales"                        # sales, account executive, business development
    ADMIN = "admin"                        # administrasi, back office, data entry, sekretaris
    CUSTOMER_SERVICE = "customer_service"  # customer service, call center, teller/frontliner
    FINANCE = "finance"                    # akuntansi, keuangan, pajak, perbankan, asuransi
    HR = "hr"                              # HR, rekrutmen, people/GA
    OPERATIONS = "operations"              # operasional, logistik, gudang, supply chain, purchasing, produksi
    ENGINEERING = "engineering"            # teknik non-IT: sipil, mesin, elektro, konstruksi
    EDUCATION = "education"                # guru, tutor, pengajar, trainer
    HEALTHCARE = "healthcare"              # perawat, apoteker, tenaga medis
    HOSPITALITY = "hospitality"            # hotel, F&B, retail toko, pariwisata
    OTHER = "other"                        # lainnya


class WorkMode(StrEnum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"


class EmploymentType(StrEnum):
    FULLTIME = "fulltime"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"
    PARTTIME = "parttime"


class ExperienceLevel(StrEnum):
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class ApplyMode(StrEnum):
    EMAIL = "email"
    PORTAL = "portal"


class JobStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"
    SAVED = "saved"
    HIDDEN = "hidden"
    APPLIED = "applied"


class SourceType(StrEnum):
    API = "api"
    HTML = "html"
    EMAIL = "email"
    SERPAPI = "serpapi"


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    REPLIED = "replied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class EmailDirection(StrEnum):
    OUT = "out"
    IN = "in"
