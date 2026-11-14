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
    OTHER = "other"            # non-IT


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
