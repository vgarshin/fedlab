// Content of the Manual page. Every text exists in English and Russian and is shown side by side.
// "admin" / "viewer" decide which role sees an item; items without a role are shown to everyone.
export type Lang = "en" | "ru";
export type L = Record<Lang, string>;
export type LL = Record<Lang, string[]>;
export type Role = "admin" | "viewer";

export interface PageDoc { name: L; path: string; adminOnly?: boolean; purpose: L; viewer?: LL; admin: LL }
export interface Step { en: string; ru: string }

export const intro: { title: L; text: LL } = {
  title: { en: "How the app works", ru: "Как работает приложение" },
  text: {
    en: [
      "The app grades team case presentations (PDF) from Moodle with the help of AI models, step by step. Every step stores its result, so nothing is lost if something stops, and every result can be opened and checked.",
      "Source files → 1. Load the Moodle export (zip + grading worksheet CSV) → 2. Extract text and technical facts from each PDF (slides, characters, fonts, links); works that fail the technical rules get 0 without any AI call → 3. AI detection: the probability that the text was written by AI (information only, never changes a grade) → 4. Grading: the AI scores six criteria from 1 to 10 with evidence from the slides; the 100-point total is calculated by the program, not by the AI → 5. Export: the Moodle worksheet with grades and feedback, per-work feedback files and a jury table.",
      "Work is organised in runs. A run is one grading session over one Moodle assignment.",
    ],
    ru: [
      "Приложение оценивает командные кейс-презентации (PDF) из Moodle с помощью ИИ-моделей, шаг за шагом. Каждый шаг сохраняет результат, поэтому при сбое ничего не теряется, а любой результат можно открыть и проверить.",
      "Исходные файлы → 1. Загрузка выгрузки Moodle (zip + CSV ведомости) → 2. Извлечение текста и технических фактов из каждого PDF (слайды, знаки, шрифты, ссылки); работы, не прошедшие технические правила, получают 0 без обращения к ИИ → 3. ИИ-детекция: вероятность того, что текст написан ИИ (только справочно, оценку не меняет) → 4. Оценивание: ИИ ставит шесть оценок от 1 до 10 с доказательствами по слайдам; итог по 100-балльной шкале считает программа, а не ИИ → 5. Экспорт: ведомость Moodle с оценками и обратной связью, файлы обратной связи по каждой работе и таблица для жюри.",
      "Работа организована по запускам (run). Запуск — это одна сессия оценивания одного задания Moodle.",
    ],
  },
};

export const roles: { title: L; viewer: LL; admin: LL } = {
  title: { en: "Roles", ru: "Роли" },
  viewer: {
    en: ["Viewer: sees runs, single works, results and analytics, downloads result files and can try prompts in the Playground. A viewer cannot start or change anything."],
    ru: ["Наблюдатель (viewer): видит запуски, отдельные работы, результаты и аналитику, скачивает файлы результатов и может пробовать промпты в «Playground». Запускать и менять что-либо нельзя."],
  },
  admin: {
    en: ["Admin: full access — creates runs, loads files, starts stages, edits prompts and settings, manages users and API keys.", "Viewer: read-only access (see the “Viewer manual” switch above to read what viewers see)."],
    ru: ["Администратор (admin): полный доступ — создаёт запуски, загружает файлы, запускает этапы, правит промпты и настройки, управляет пользователями и API-ключами.", "Наблюдатель (viewer): доступ только на чтение (переключатель «Инструкция наблюдателя» выше показывает, что видят наблюдатели)."],
  },
};

export const pagesTitle: L = { en: "Pages and their key functions", ru: "Страницы и их основные функции" };

export const pages: PageDoc[] = [
  { name: { en: "Runs", ru: "Запуски" }, path: "/", purpose: { en: "List of all grading runs.", ru: "Список всех запусков оценивания." },
    viewer: { en: ["Open a run to see its progress and results."], ru: ["Откройте запуск, чтобы увидеть ход и результаты."] },
    admin: { en: ["Create a new run (“+ Create run”).", "Open a run to work with it."], ru: ["Создать новый запуск («+ Create run»).", "Открыть запуск для работы."] } },
  { name: { en: "Run", ru: "Запуск" }, path: "/runs/…", purpose: { en: "Everything about one run: progress of every stage and the list of works.", ru: "Всё об одном запуске: ход каждого этапа и список работ." },
    viewer: { en: ["Works table: slides, characters, technical check, AI probability, grade. Warnings are shown (for example “over limit”).", "The Flags column shows reviewer hints: possible manipulation (hidden or tiny text, phrases aimed at the grader, a suspicion from the AI check) and PARTIALLY_READABLE — some slides had no usable text layer (image-only or garbled); their text, if any, came from OCR. They never change a grade; open the work to see the details.",
              "Stage strip shows how far each stage has gone.", "“Results (zip)” downloads every result file of the run; the Export block lists the run-level files.", "Click a work name to open the work."],
              ru: ["Таблица работ: слайды, знаки, техническая проверка, вероятность ИИ, оценка. Предупреждения видны (например, «over limit» — превышение объёма).", "Колонка Flags показывает подсказки проверяющему: возможную манипуляцию (скрытый или мелкий текст, фразы, обращённые к оценивающему ИИ, подозрение по результату ИИ-проверки) и PARTIALLY_READABLE — часть слайдов без машиночитаемого текста (картинки или нечитаемый слой); их текст, если он есть, распознан через OCR. Оценку они не меняют; подробности — в карточке работы.",
              "Полоса этапов показывает, как далеко продвинулся каждый этап.", "«Results (zip)» скачивает все файлы результатов запуска; блок Export перечисляет файлы запуска.", "Нажмите на название работы, чтобы открыть её."] },
    admin: { en: ["“▶ Resume / run all stages” runs stages 2 → 3 → 4 → export for every work; finished work is skipped, so it is also the “resume” button. “Retry failed” appears if something failed.",
                  "Source files: files always come as a BATCH — a folder incoming/<name>/ that is created once with a new name (default YYYYMMDD-HHMM) and never extended. Three ways: upload the Moodle zip (+ worksheet CSV) from your computer; reserve a name and copy the files with the AWS CLI; or pick an existing batch. A batch with two zips or two CSVs, or one changed after use, is marked and cannot be used. Late files = a new batch added to the same run.",
                  "Stage strip: each stage has its own Resume / run button; the OCR switch in stage 2 recognises slides that have no text.",
                  "Configuration & cost: which model and prompt are used, money spent against the budget cap.",
                  "Export: “Rebuild export” recreates all result files from the stored grades (free); “Results (zip)” downloads them.",
                  "Danger zone · delete run: removes this run with all its works and files (type DELETE to confirm; cannot be undone). Four progress bars follow the deletion — source files, work files, exported results, database records — the current one moves with a done/total count, finished ones show a ✓, and you are returned to the Runs list when it finishes. If a stage fails, the run is marked failed and you can simply press “Delete run” again: finished stages are skipped. Other runs, the shared answer cache and the source batches are not touched."],
             ru: ["«▶ Resume / run all stages» выполняет этапы 2 → 3 → 4 → экспорт для всех работ; готовые работы пропускаются, поэтому это же и кнопка «продолжить». Если что-то не удалось, появляется «Retry failed».",
                  "Source files: файлы всегда приходят ПАКЕТОМ (batch) — папкой incoming/<имя>/, которая создаётся один раз с новым именем (по умолчанию ГГГГММДД-ЧЧММ) и никогда не дополняется. Три способа: загрузить zip Moodle (+ CSV ведомости) со своего компьютера; зарезервировать имя и скопировать файлы через AWS CLI; выбрать существующий пакет. Пакет с двумя zip или двумя CSV, а также изменённый после использования, помечается и не может быть использован. Поздние файлы = новый пакет, добавленный в тот же запуск.",
                  "Полоса этапов: у каждого этапа своя кнопка Resume / run; переключатель OCR на этапе 2 распознаёт слайды без текста.",
                  "Configuration & cost: какие модель и промпт используются, потрачено денег относительно лимита бюджета.",
                  "Export: «Rebuild export» заново создаёт все файлы результатов из сохранённых оценок (бесплатно); «Results (zip)» скачивает их.",
                  "Danger zone · delete run: полностью удаляет этот запуск вместе со всеми работами и файлами (введите слово DELETE для подтверждения; отменить нельзя). Удаление идёт по четырём полосам прогресса — исходные файлы, рабочие файлы, результаты экспорта, записи базы данных: текущая полоса движется со счётчиком «сделано / всего», завершённые отмечены галочкой, по завершении вы возвращаетесь к списку запусков. Если этап не удался, запуск помечается как failed и можно просто снова нажать «Delete run»: готовые этапы пропускаются. Другие запуски, общий кэш ответов и исходные пакеты не затрагиваются."] } },
  { name: { en: "Work", ru: "Работа" }, path: "/runs/…/teams/…", purpose: { en: "One team's work with every step of its processing.", ru: "Одна работа команды со всеми шагами её обработки." },
    viewer: { en: ["The PDF, the technical check and its metrics.", "AI detection result with reasons (information only).", "Grade: six criteria with scores, feedback and evidence from slides, total out of 100.", "Extracted text slide by slide; all stored files; calls to the AI and their cost.", "“Result files of this work”: download the feedback files or all of them as a zip."],
              ru: ["PDF, техническая проверка и её метрики.", "Результат ИИ-детекции с причинами (только справочно).", "Оценка: шесть критериев с баллами, обратной связью и доказательствами по слайдам, итог из 100.", "Извлечённый текст по слайдам; все сохранённые файлы; обращения к ИИ и их стоимость.", "«Result files of this work»: скачайте файлы обратной связи или все сразу в zip."] },
    admin: { en: ["Same view as a viewer. Use it to check a grade before exporting: read the evidence, compare with the PDF, look at the warnings and flags."],
             ru: ["Тот же вид, что у наблюдателя. Используйте его, чтобы проверить оценку перед экспортом: прочитайте доказательства, сверьтесь с PDF, посмотрите предупреждения и флаги."] } },
  { name: { en: "Analytics", ru: "Аналитика" }, path: "/analytics", purpose: { en: "Summary of a run.", ru: "Сводка по запуску." },
    viewer: { en: ["Choose a run (the latest is the default).", "KPIs: works, members, graded, disqualified, average and median grade, money spent.", "Histograms of grades and of text lengths (the 10 000-character limit is marked).", "Top 5 best and 5 lowest graded works."],
              ru: ["Выберите запуск (по умолчанию последний).", "Показатели: работы, участники, оценено, снято, средняя и медианная оценки, потрачено денег.", "Гистограммы оценок и длин текстов (граница 10 000 знаков отмечена).", "5 лучших и 5 самых низких оценённых работ."] },
    admin: { en: ["Same as for viewers."], ru: ["То же, что у наблюдателей."] } },
  { name: { en: "Playground", ru: "Playground" }, path: "/playground", purpose: { en: "Try a prompt and a model on one work without changing the run.", ru: "Пробовать промпт и модель на одной работе, не меняя запуск." },
    viewer: { en: ["Choose a run, a work, a prompt and a model; the PDF is shown next to the model's answer.", "“Fresh call” forces a new paid answer; otherwise an identical earlier answer comes from the cache for free.", "Save the result (.json), the raw answer (.txt) or a full report (.json) to your computer.", "Each experiment costs money and counts toward the run's budget cap."],
              ru: ["Выберите запуск, работу, промпт и модель; PDF показан рядом с ответом модели.", "«Fresh call» принудительно запрашивает новый платный ответ; иначе одинаковый прежний ответ берётся из кэша бесплатно.", "Сохраните результат (.json), «сырой» ответ (.txt) или полный отчёт (.json) на компьютер.", "Каждый эксперимент стоит денег и учитывается в лимите бюджета запуска."] },
    admin: { en: ["Use it to test a draft prompt before publishing, or to compare models on one work."], ru: ["Используйте для проверки черновика промпта до публикации или для сравнения моделей на одной работе."] } },
  { name: { en: "Prompts", ru: "Промпты" }, path: "/prompts", adminOnly: true, purpose: { en: "The instructions given to the AI.", ru: "Инструкции, которые получает ИИ." },
    admin: { en: ["Prompts are versioned and never edited in place: “Edit as new version” saves a draft.", "Draft = not used by any run. “Publish” makes it eligible; stages use the latest published version of the prompt chosen in Settings.", "Show the difference between versions; test a version in the Playground."],
             ru: ["Промпты версионируются и не правятся «на месте»: «Edit as new version» сохраняет черновик.", "Черновик не используется ни одним запуском. «Publish» делает его доступным; этапы берут последнюю опубликованную версию промпта, выбранного в Settings.", "Можно показать различия между версиями; протестировать версию в Playground."] } },
  { name: { en: "Models", ru: "Модели" }, path: "/models", adminOnly: true, purpose: { en: "The list of AI models and their prices.", ru: "Список ИИ-моделей и их цены." },
    admin: { en: ["Switch models on or off. Prices (per million tokens) are shown for information."], ru: ["Включение и выключение моделей. Цены (за миллион токенов) показаны для информации."] } },
  { name: { en: "Settings", ru: "Настройки" }, path: "/settings", adminOnly: true, purpose: { en: "Global choices for all runs.", ru: "Общие настройки для всех запусков." },
    admin: { en: ["Model and prompt for grading and for AI detection; the switch for stage 3 (AI detection).", "OCR: maximum slides recognised per work. Budget limits: spending cap per run (grading, AI detection and OCR together).", "Export wording (first line of the feedback) and the CSV delimiter for the Moodle worksheet.", "Danger zone: clear the cache of AI answers and OCR pages (re-running will cost money again)."],
             ru: ["Модель и промпт для оценивания и для ИИ-детекции; переключатель этапа 3 (ИИ-детекция).", "OCR: максимум слайдов на одну работу. Budget limits: лимит расходов на запуск (оценивание, ИИ-детекция и OCR вместе).", "Формулировки экспорта (первая строка обратной связи) и разделитель CSV для ведомости Moodle.", "Danger zone: очистка кэша ответов ИИ и страниц OCR (повторный запуск снова будет стоить денег)."] } },
  { name: { en: "Users", ru: "Пользователи" }, path: "/users", adminOnly: true, purpose: { en: "Who can sign in.", ru: "Кто может войти." },
    admin: { en: ["Add a person by their Yandex e-mail and choose a role (viewer or admin).", "Change a role or switch a person off."], ru: ["Добавьте человека по e-mail Яндекса и выберите роль (viewer или admin).", "Смена роли или отключение человека."] } },
  { name: { en: "API", ru: "API" }, path: "/developers", adminOnly: true, purpose: { en: "Connect other programs.", ru: "Подключение других программ." },
    admin: { en: ["Create API keys (read-only or full), shown once; revoke them at any time.", "A live reference of all API methods with the role each one needs and example calls."], ru: ["Создание API-ключей (только чтение или полный доступ), показываются один раз; можно отозвать в любой момент.", "Актуальный справочник всех методов API с нужной ролью и примерами вызовов."] } },
  { name: { en: "Manual", ru: "Инструкция" }, path: "/manual", purpose: { en: "This page.", ru: "Эта страница." },
    viewer: { en: ["Brief instruction in English and Russian."], ru: ["Краткая инструкция на английском и русском."] }, admin: { en: ["Brief instruction in English and Russian; admins can also read the viewer version."], ru: ["Краткая инструкция на английском и русском; администраторы могут прочитать и версию для наблюдателей."] } },
];

export const workflow: { title: L; viewer: Step[]; admin: Step[] } = {
  title: { en: "Typical workflow", ru: "Обычный порядок работы" },
  viewer: [
    { en: "Open Runs and choose a run.", ru: "Откройте «Runs» и выберите запуск." },
    { en: "Look at the works table: grades, warnings, AI probability.", ru: "Посмотрите таблицу работ: оценки, предупреждения, вероятность ИИ." },
    { en: "Open a work to read the feedback and the evidence next to the PDF.", ru: "Откройте работу, чтобы прочитать обратную связь и доказательства рядом с PDF." },
    { en: "Open Analytics for the overall picture.", ru: "Откройте «Analytics» для общей картины." },
    { en: "Download results: “Results (zip)” on the run, or the files of one work.", ru: "Скачайте результаты: «Results (zip)» в запуске или файлы одной работы." },
  ],
  admin: [
    { en: "Settings: check the model and prompt, the AI-detection switch and the budget cap.", ru: "Settings: проверьте модель и промпт, переключатель ИИ-детекции и лимит бюджета." },
    { en: "Runs → “+ Create run” with a clear name.", ru: "Runs → «+ Create run» с понятным названием." },
    { en: "Open the run → Source files: upload the Moodle zip and CSV, or choose a batch copied to incoming/. While the files are unpacked a live progress bar shows the phases (reading → unpacking → registering teams); wait until the status is “ready”.", ru: "Откройте запуск → Source files: загрузите zip Moodle и CSV либо выберите пакет, скопированный в incoming/. Пока файлы распаковываются, живой прогресс-бар показывает этапы (чтение → распаковка → регистрация работ); дождитесь статуса «ready»." },
    { en: "If some PDFs are scans, switch OCR on in the 2 Extract block.", ru: "Если среди PDF есть сканы, включите OCR в блоке 2 Extract." },
    { en: "Press “▶ Resume / run all stages” and watch the progress (it updates by itself).", ru: "Нажмите «▶ Resume / run all stages» и следите за ходом (он обновляется сам)." },
    { en: "Check the works: open any work, read the evidence; look at warnings (over limit, technical failures, needs review).", ru: "Проверьте работы: откройте любую, прочитайте доказательства; обратите внимание на предупреждения (превышение объёма, технические отказы, «needs review»)." },
    { en: "Export → “Rebuild export”, then “Results (zip)”. Use the filled worksheet CSV to upload grades to Moodle.", ru: "Export → «Rebuild export», затем «Results (zip)». Заполненный CSV ведомости используйте для загрузки оценок в Moodle." },
    { en: "Analytics: check the distribution of grades and the top / lowest works.", ru: "Analytics: проверьте распределение оценок и лучшие / худшие работы." },
  ],
};

export const tips: { title: L; en: string[]; ru: string[] } = {
  title: { en: "Good to know", ru: "Полезно знать" },
  en: [
    "Grades are whole numbers out of 100: six criteria of 1–10, the total is calculated by the program.",
    "A work is not graded by the AI (grade 0 with a reason) if it is not a PDF, has no author / team name, or has no machine-readable text (unless OCR is on). A text over 10 000 characters is only a warning.",
    "Equal totals (ties) are only marked; the jury decides the ranking.",
    "Never add files to an existing batch: make a new batch (a new name) instead. The app refuses an existing name and flags batches that were changed.",
    "AI probability and the Flags column (possible manipulation of the grader, PARTIALLY_READABLE slides) are information for reviewers only and never change a grade.",
    "Repeating the same request is free: answers are cached. Changing the prompt, model or text makes a new paid request.",
    "Every run has a spending cap (Settings). When it is reached, remaining works stop with a clear message.",
  ],
  ru: [
    "Оценки — целые числа из 100: шесть критериев по 1–10, итог считает программа.",
    "Работа не оценивается ИИ (оценка 0 с указанием причины), если это не PDF, не указана команда / автор или нет машиночитаемого текста (если не включён OCR). Объём больше 10 000 знаков — только предупреждение.",
    "Одинаковые итоги (ничьи) только отмечаются; ранжирование определяет жюри.",
    "Не добавляйте файлы в существующий пакет: создайте новый пакет (новое имя). Приложение не принимает уже существующее имя и помечает изменённые пакеты.",
    "Вероятность ИИ и колонка Flags (возможная манипуляция оценивающим ИИ, PARTIALLY_READABLE — часть слайдов без текста) — информация для проверяющих, оценку они не меняют.",
    "Повтор того же запроса бесплатен: ответы кэшируются. Смена промпта, модели или текста даёт новый платный запрос.",
    "У каждого запуска есть лимит расходов (Settings). Когда он достигнут, оставшиеся работы останавливаются с понятным сообщением.",
  ],
};
