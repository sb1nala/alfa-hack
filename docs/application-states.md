# Статусы заявки

Контракт для всей команды: бэкенд реализует переходы, фронтенд рисует таймлайн, риски выбирают исход, продукт пишет тексты к каждому статусу. Новый статус или переход — сначала правка этого файла.

```mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> SUBMITTED: клиент отправил анкету
    SUBMITTED --> CHECKING: анкета валидна
    CHECKING --> CHECKING: внешний сервис не ответил, ретрай
    CHECKING --> SCORING: БКИ, ЕГРЮЛ/ЕГРИП, ФНС получены
    SCORING --> APPROVED
    SCORING --> COUNTER_OFFER
    SCORING --> NEEDS_INFO
    SCORING --> MANUAL_REVIEW
    SCORING --> REJECTED
    NEEDS_INFO --> CHECKING: клиент дополнил данные
    MANUAL_REVIEW --> APPROVED: решение аналитика
    MANUAL_REVIEW --> COUNTER_OFFER: решение аналитика
    MANUAL_REVIEW --> NEEDS_INFO: решение аналитика
    MANUAL_REVIEW --> REJECTED: решение аналитика
    APPROVED --> OFFER_ACCEPTED: клиент принял условия
    COUNTER_OFFER --> OFFER_ACCEPTED: клиент принял контрпредложение
    COUNTER_OFFER --> CANCELLED: клиент отказался
    OFFER_ACCEPTED --> SIGNED: подписание (симуляция)
    SIGNED --> ISSUED: выдача (симуляция)
    REJECTED --> [*]
    ISSUED --> [*]
    CANCELLED --> [*]
```

Клиент может отозвать заявку в `CANCELLED` из любого статуса до `SIGNED`.

| Статус | Что видит клиент | Кто переводит дальше |
| --- | --- | --- |
| `DRAFT` | Черновик анкеты | Клиент |
| `SUBMITTED` | Заявка отправлена, проверяем анкету | Система |
| `CHECKING` | Запрашиваем данные из БКИ, ЕГРЮЛ/ЕГРИП, ФНС | Worker |
| `SCORING` | Оцениваем заявку | Worker (`decide()`) |
| `NEEDS_INFO` | Нужны дополнительные данные — что именно и зачем | Клиент |
| `MANUAL_REVIEW` | Заявку смотрит специалист | Андеррайтер |
| `APPROVED` | Одобрено на запрошенных условиях | Клиент |
| `COUNTER_OFFER` | Готовы выдать на других условиях (сумма, срок, продукт) | Клиент |
| `REJECTED` | Отказ с понятной причиной | — |
| `OFFER_ACCEPTED` | Условия приняты, готовим документы | Клиент |
| `SIGNED` | Договор подписан (симуляция) | Система |
| `ISSUED` | Деньги выданы (симуляция) | — |
| `CANCELLED` | Заявка отозвана | — |
