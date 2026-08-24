import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

NAV_KEYWORDS = (
    "messages",
    "viestit",
    "schedule",
    "lukujärjestys",
    "gradebook",
    "assessments",
    "exams",
    "attendance",
    "poissaolot",
    "printouts",
    "news",
)


@dataclass
class Student:
    number: str
    name: str
    user_id: str


def parse_students_from_home(html: str) -> list[Student]:
    soup = BeautifulSoup(html, "html.parser")
    students: dict[str, Student] = {}

    for anchor in soup.select("a[href^='/!']"):
        href = anchor.get("href", "")
        match = re.search(r"/!(\d+)/", href)
        if not match:
            continue

        number = match.group(1)
        cloned = BeautifulSoup(str(anchor), "html.parser").find("a")
        if not cloned:
            continue

        for tag in cloned.select("small, span.lem"):
            tag.decompose()

        text = cloned.get_text(strip=True)
        if not text:
            continue

        lower = text.lower()
        if any(keyword in lower for keyword in NAV_KEYWORDS):
            continue

        if number not in students:
            students[number] = Student(number=number, name=text, user_id=f"!{number}")

    return list(students.values())
