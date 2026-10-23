from pydantic import BaseModel, Field

class ClientApplicationRequest(BaseModel):
    inn: str = Field(..., min_length=10, max_length=12, description="ИНН заемщика (10 или 12 цифр)")
    amount: float = Field(..., gt=0, description="Запрашиваемая сумма кредита или лизинга")
    term_months: int = Field(..., gt=0, description="Срок финансирования в месяцах")

class ApplicationRouteResponse(BaseModel):
    application_id: int
    company_name: str
    status: str
    score: float
    reasons: list[str]
