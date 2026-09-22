# AGENTS.md

## Projekt
Repozytorium: `Wscieklosc/DIGI-NETRA`
Gałąź robocza: `lyr-refactor`

Projekt jest rozwinięciem i udoskonaleniem oryginalnego repozytorium:
`pwnxotus/DIGI-NETRA`

## Pochodzenie i autorstwo
- Zachowuj jasną informację, że projekt jest forkiem i rozwinięciem oryginału.
- Nie usuwaj informacji o pierwotnym autorze ani pochodzeniu projektu.
- Nie przedstawiaj całego projektu jako stworzonego od zera przez Wscieklosc.
- Własne zmiany opisuj jako poprawki, refaktoryzację, rozwinięcia i rozszerzenia.
- Nie dodawaj nowej licencji ani nie zmieniaj warunków licencyjnych bez wyraźnej decyzji użytkownika.
- Przed publikacją wydania, produktu albo większej redystrybucji sprawdź status licencji upstreamu.
- Nie twórz Pull Requesta do upstreamu bez wyraźnej prośby użytkownika.

## Nadrzędna zasada jakości detekcji OSINT
Zasada obowiązuje cały projekt: moduły dotyczące username, e-maili, numerów telefonu, adresów IP oraz wszystkie przyszłe moduły, na przykład domeny.

Maksymalizuj skuteczność wykrywania, ale nigdy kosztem fałszywych pozytywów.

Nie traktuj pojedynczego słabego sygnału jako potwierdzenia.

Priorytet klasyfikacji:
1. pewne `FOUND`,
2. pewne `NOT_FOUND`,
3. `POSSIBLE`,
4. `BLOCKED` / `RATE_LIMIT`,
5. `UNKNOWN` / `ERROR`.

Jeśli serwis lub źródło nie pozwala na wiarygodne potwierdzenie, zwróć `POSSIBLE` albo `UNKNOWN` zamiast zgadywać.

Jeżeli to możliwe, wykorzystuj kombinację wielu stabilnych sygnałów:
- status HTTP,
- final URL,
- redirecty,
- komunikaty błędów,
- markery HTML,
- canonical/meta,
- JSON osadzony w stronie,
- charakterystyczne odpowiedzi publicznych endpointów/API,
- inne stabilne, publicznie dostępne sygnały.

Dla każdej nowej reguły detekcji:
- przetestuj co najmniej jeden przypadek istniejący,
- przetestuj co najmniej jeden przypadek nieistniejący,
- jeśli ma znaczenie, przetestuj ASCII i Unicode,
- dodaj testy jednostkowe,
- nie używaj prywatnych cookies, tokenów ani zalogowanych sesji,
- nie obchodź zabezpieczeń dostępu, logowania ani antybot.

Ta zasada obowiązuje cały projekt i wszystkie przyszłe rozszerzenia.

## Git i remotes
- `origin` = `https://github.com/Wscieklosc/DIGI-NETRA.git`
- `upstream` = `https://github.com/pwnxotus/DIGI-NETRA.git`
- Domyślnie pracuj na `lyr-refactor`.
- Nie pracuj bezpośrednio na upstreamie.
- Nie zmieniaj `origin` ani `upstream` bez wyraźnej zgody użytkownika.
- Nie rób `git add`, `git commit`, `git push`, merge ani rebase bez wyraźnej prośby użytkownika.
- Nie twórz nowych branchy bez wyraźnej prośby.

## Środowisko Python
Projekt lokalnie używa osobnego venv:
`/mnt/ai/models/srodowisko_pythona/DIGI-NETRA/.venv`

- Nie instaluj pakietów globalnie, jeśli nie jest to konieczne.
- Pythonowe zależności projektu instaluj do aktywnego `.venv`.
- Jeśli dodajesz zależność potrzebną projektowi, uzupełnij `requirements.txt`.
- Nie dodawaj `.venv/`, `__pycache__/` ani plików cache do repo.

## Styl pracy
- Jedno zadanie naraz.
- Jedna zmiana naraz.
- Preferuj małe i łatwe do zweryfikowania poprawki.
- Nie przebudowuj dużych fragmentów projektu, jeśli wystarczy mała zmiana.
- Nie zgaduj działania serwisu po samym `HTTP 200`.
- Przy OSINT preferuj pewność wyniku nad liczbą wyników.
- Nie obchodź logowania, zabezpieczeń antybot ani kontroli dostępu.
- Działaj wyłącznie na danych publicznych i legalnie dostępnych.

## Username search
Aktualna logika rozróżnia statusy:
- `FOUND`
- `NOT_FOUND`
- `POSSIBLE`
- `UNKNOWN`
- `BLOCKED`
- `RATE_LIMIT`
- `ERROR`

Zasady:
- `FOUND` tylko przy konkretnym, wiarygodnym potwierdzeniu.
- `POSSIBLE` oznacza kandydata, nie potwierdzone konto.
- `HTTP 200` sam w sobie nie jest dowodem istnienia profilu.
- Jeśli serwis wymaga specjalnej detekcji, dodaj czytelną regułę lub osobny checker zamiast fałszywego pozytywu.
- Zachowaj obsługę Unicode w nazwach użytkowników przez prawidłowe kodowanie URL.

## Konfiguracja serwisów
Główna konfiguracja:
`main/murl.json`

- JSON musi pozostać poprawny składniowo.
- Nie dodawaj serwisu bez sensownej metody rozróżnienia `FOUND` / `NOT_FOUND` / `POSSIBLE`.
- Nie kopiuj ślepo generycznego `Page Not Found` jako jedynej reguły.
- Dla trudnych serwisów dopuszczalne są pola takie jak:
  - `found_status_codes`
  - `not_found_status_codes`
  - `found_message`
  - `not_found_message`
  - `cookies`
  - `needs_login`

## Aktualny potwierdzony przypadek
YouTube:
- używa cookie `SOCS=CAI`,
- `HTTP 200` po przejściu strony consent może potwierdzić istniejący kanał,
- `HTTP 404` oznacza brak kanału.

Nie zakładaj, że ta sama reguła działa dla innych serwisów.

## Testy po zmianach
Po zmianach w `main/username.py`:
`python -m py_compile main/username.py`

Po zmianach w `main/murl.json`:
`python -m json.tool main/murl.json >/dev/null && echo "JSON OK"`

Przed uznaniem większej zmiany za zakończoną:
- sprawdź składnię,
- sprawdź JSON,
- wykonaj test na jednym znanym istniejącym i jednym celowo nieistniejącym username, jeśli zmiana dotyczy detekcji.

## Bezpieczeństwo zmian
- Nie usuwaj plików bez wyraźnej zgody użytkownika.
- Nie usuwaj informacji o oryginalnym autorze.
- Nie modyfikuj README w sposób sugerujący pełne autorstwo Wscieklosc.
- Nie dodawaj sekretów, tokenów, cookies sesyjnych ani kluczy API do repo.
- Jeśli wykryjesz sekret w pliku, zatrzymaj się i poinformuj użytkownika.

## Preferencje użytkownika
- Użytkownik chce prowadzenia krok po kroku.
- Dla terminala i programowania podawaj jedną rzecz naraz.
- Każdą komendę opisuj: gdzie ją wpisać, jaki użytkownik i czy wymaga `sudo`.
- Nie dawaj kilku alternatywnych dróg naraz.
- Jeśli istnieje kilka dobrych sposobów, wybierz najlepszy i prowadź nim.

## Priorytet
1. Zachowanie autorstwa i pochodzenia projektu.
2. Bezpieczeństwo oraz legalny charakter OSINT.
3. Brak fałszywych pozytywów.
4. Stabilność projektu.
5. Małe i czytelne zmiany.
6. Rozbudowa funkcji.

## Raport
Raportuj krótko:
1. Co zmieniono.
2. Co sprawdzono.
3. Jaki jest wynik.
4. Czy jest coś do kontroli.

## .codex
- Folder `.codex/` jest lokalnym miejscem roboczym Codexa.
- Codex może tam przechowywać prywatne notatki, kontekst projektu i pomocnicze informacje operacyjne.
- `.codex/` nie jest częścią publicznego repozytorium i pozostaje w `.gitignore`.
- Nie zapisuj tam sekretów, tokenów, haseł ani danych uwierzytelniających.
- Oficjalne zmiany projektu wykonuj wyłącznie w normalnych plikach repozytorium.
