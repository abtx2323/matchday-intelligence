from __future__ import annotations

from dataclasses import dataclass

from ..schema import Audience, Language, Mode


@dataclass(frozen=True)
class ViewerProfile:
    mode: Mode = Mode.CASUAL
    language: Language = Language.EN
    favorite_club: str | None = None
    focus_player: str | None = None

    @property
    def key(self) -> str:
        return f"{self.mode}|{self.language}|{self.favorite_club or '-'}|{self.focus_player or '-'}"

    def audience(self) -> Audience:
        return Audience(
            mode=self.mode,
            language=self.language,
            favorite_club=self.favorite_club,
            focus_player=self.focus_player,
        )
