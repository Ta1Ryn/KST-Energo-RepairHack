# Запуск

Актуальные инструкции, архитектура и изображения находятся в [README.md](README.md).

Консоль: `./Sensor-Console.sh`. Команды: `normal`, `warning`, `alarm`, `heat 20`, `cool 20`, `pressure 8 5`, `vibration 6 5`, `status`, `exit`. Телеметрия тестовая. Пороги давления 7 и 10 бар условные.

Остановка: `./Stop-Integrated.sh`. Серверная проверка: `node tests/integrated.mjs` — требует PostgreSQL и готового CV. Во время демонстрации не запускайте тест cv-specialists: он меняет активный профиль моделей.
