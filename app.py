import streamlit as st
import re
import psycopg2
import pandas as pd
from langchain_ollama import ChatOllama

# === НАСТРОЙКИ ИНТЕРФЕЙСА ===
st.set_page_config(page_title="AI SQL Аналитик", layout="wide")
st.title("Локальный AI-агент аналитик для PostgreSQL")
st.markdown("Работает автономно через Ollama. Без API-ключей.")

# === ИНИЦИАЛИЗАЦИЯ LLM ===
@st.cache_resource
def get_llm():
    return ChatOllama(
        model="qwen2.5-coder:7b",
        temperature=0,
        base_url="http://ollama:11434"
    )

llm = get_llm()

# === СХЕМА БД (CHINOOK - реальные имена в нижнем регистре) ===
SCHEMA_DESCRIPTION = """
СТРУКТУРА БАЗЫ ДАННЫХ CHINOOK (Музыкальный магазин).
Все имена таблиц и колонок в нижнем регистре. НЕ используй кавычки.

Таблица customer:
- customer_id (INT, PK) - ID клиента
- first_name (VARCHAR) - имя
- last_name (VARCHAR) - фамилия
- company (VARCHAR) - компания
- address (VARCHAR) - адрес
- city (VARCHAR) - город
- state (VARCHAR) - штат
- country (VARCHAR) - страна
- postal_code (VARCHAR) - почтовый индекс
- phone (VARCHAR) - телефон
- fax (VARCHAR) - факс
- email (VARCHAR) - email
- support_rep_id (INT) - ID менеджера

Таблица invoice:
- invoice_id (INT, PK) - ID счёта
- customer_id (INT, FK -> customer) - ID клиента
- invoice_date (TIMESTAMP) - дата покупки
- billing_address (VARCHAR) - адрес выставления счёта
- billing_city (VARCHAR) - город
- billing_state (VARCHAR) - штат
- billing_country (VARCHAR) - страна
- billing_postal_code (VARCHAR) - почтовый индекс
- total (NUMERIC) - сумма счёта

Таблица invoice_line:
- invoice_line_id (INT, PK)
- invoice_id (INT, FK -> invoice) - ID счёта
- track_id (INT, FK -> track) - ID трека
- unit_price (NUMERIC) - цена за единицу
- quantity (INT) - количество

Таблица track:
- track_id (INT, PK)
- name (VARCHAR) - название трека
- album_id (INT, FK -> album) - ID альбома
- media_type_id (INT, FK -> media_type) - ID типа медиа
- genre_id (INT, FK -> genre) - ID жанра
- composer (VARCHAR) - композитор
- milliseconds (INT) - длительность в миллисекундах
- bytes (INT) - размер в байтах
- unit_price (NUMERIC) - цена трека

Таблица album:
- album_id (INT, PK)
- title (VARCHAR) - название альбома
- artist_id (INT, FK -> artist) - ID исполнителя

Таблица artist:
- artist_id (INT, PK)
- name (VARCHAR) - имя исполнителя

Таблица genre:
- genre_id (INT, PK)
- name (VARCHAR) - название жанра

Таблица media_type:
- media_type_id (INT, PK)
- name (VARCHAR) - название типа медиа

Таблица playlist:
- playlist_id (INT, PK)
- name (VARCHAR) - название плейлиста

Таблица playlist_track:
- playlist_id (INT, FK -> playlist) - ID плейлиста
- track_id (INT, FK -> track) - ID трека

Таблица employee:
- employee_id (INT, PK)
- last_name (VARCHAR) - фамилия
- first_name (VARCHAR) - имя
- title (VARCHAR) - должность
- reports_to (INT) - ID руководителя
- birth_date (TIMESTAMP) - дата рождения
- hire_date (TIMESTAMP) - дата найма
- address (VARCHAR) - адрес
- city (VARCHAR) - город
- state (VARCHAR) - штат
- country (VARCHAR) - страна
- postal_code (VARCHAR) - почтовый индекс
- phone (VARCHAR) - телефон
- fax (VARCHAR) - факс
- email (VARCHAR) - email

СВЯЗИ МЕЖДУ ТАБЛИЦАМИ:
- invoice.customer_id -> customer.customer_id
- invoice_line.invoice_id -> invoice.invoice_id
- invoice_line.track_id -> track.track_id
- track.album_id -> album.album_id
- track.media_type_id -> media_type.media_type_id
- track.genre_id -> genre.genre_id
- album.artist_id -> artist.artist_id
- playlist_track.playlist_id -> playlist.playlist_id
- playlist_track.track_id -> track.track_id
- employee.reports_to -> employee.employee_id
- customer.support_rep_id -> employee.employee_id
"""

# === ПРОВЕРКА НАМЕРЕНИЯ ПОЛЬЗОВАТЕЛЯ ===
def check_user_intent(user_query: str):
    query_lower = user_query.lower()
    dangerous_patterns = [
        r'\bудал', r'\bdelete\b', r'\bdrop\b', r'\bстереть',
        r'\bобнов', r'\bupdate\b', r'\bизмени', r'\bпоменяй', r'\bзамени',
        r'\bдобав', r'\binsert\b', r'\bвстав', r'\bсоздай', r'\bcreate\b',
        r'\bочист', r'\btruncate\b', r'\bclear\b', r'\bwipe\b',
        r'\balter\b', r'\bмодифицируй', r'\brename\b',
        r'\bexec\b', r'\bexecute\b', r'\brun\b',
        r'\bgrant\b', r'\brevoke\b',
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, query_lower):
            return True, f"Обнаружено намерение выполнить деструктивную операцию"
    return False, ""

# === ВАЛИДАТОР БЕЗОПАСНОСТИ SQL ===
def validate_sql_safety(sql_query: str):
    sql_upper = sql_query.upper()
    dangerous = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER', 'TRUNCATE', 'GRANT', 'REVOKE', 'EXEC', 'EXECUTE']
    for keyword in dangerous:
        if re.search(r'\b' + keyword + r'\b', sql_upper):
            return False, f"Запрещенная операция в SQL: {keyword}"
    return True, ""

# === ИЗВЛЕЧЕНИЕ SQL ИЗ ОТВЕТА ===
def extract_sql_from_text(text: str):
    pattern = r'```sql\s*(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
    if matches:
        return [m.strip() for m in matches]
    return []

# === ГЕНЕРАЦИЯ SQL ===
def generate_sql(user_query: str) -> str:
    prompt_text = f"""Ты SQL-эксперт для PostgreSQL. Ты работаешь с базой данных музыкального магазина Chinook.

{SCHEMA_DESCRIPTION}

ПРИМЕРЫ ПРАВИЛЬНЫХ ЗАПРОСОВ:

Пример 1: "Покажи всех клиентов из USA"
```sql
SELECT first_name, last_name, country
FROM customer
WHERE country = 'USA'
LIMIT 20;
```

Пример 2: "Покажи общую выручку"
```sql
SELECT SUM(total) AS total_revenue
FROM invoice;
```

Пример 3: "Топ-5 исполнителей по количеству продаж"
```sql
SELECT ar.name AS artist_name, COUNT(il.track_id) AS total_sales
FROM invoice_line il
JOIN track t ON il.track_id = t.track_id
JOIN album al ON t.album_id = al.album_id
JOIN artist ar ON al.artist_id = ar.artist_id
GROUP BY ar.name
ORDER BY total_sales DESC
LIMIT 5;
```

Пример 4: "Какой жанр самый популярный?"
```sql
SELECT g.name AS genre_name, COUNT(il.track_id) AS purchase_count
FROM invoice_line il
JOIN track t ON il.track_id = t.track_id
JOIN genre g ON t.genre_id = g.genre_id
GROUP BY g.name
ORDER BY purchase_count DESC
LIMIT 1;
```

ЗАДАЧА: Преобразуй запрос пользователя в SQL-запрос.

СТРОГИЕ ПРАВИЛА:
1. Возвращай ТОЛЬКО SQL-запрос в блоке кода (```sql ... ```), без объяснений.
2. Используй ТОЛЬКО таблицы и колонки из схемы выше. НЕ выдумывай названия.
3. Все имена таблиц и колонок пиши в нижнем регистре БЕЗ кавычек.
4. Всегда добавляй LIMIT 20, если пользователь не указал количество.
5. Для дат используй CURRENT_DATE и INTERVAL или конкретные даты в формате 'YYYY-MM-DD'.

Запрос пользователя: {user_query}

SQL-запрос:"""
    
    response = llm.invoke(prompt_text)
    sql = response.content.strip()
    
    match = re.search(r'```sql\s*(.*?)```', sql, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return sql

# === ВЫПОЛНЕНИЕ SQL ===
def execute_sql(sql_query: str):
    try:
        conn = psycopg2.connect(
            host="db",
            database="postgres",
            user="readonly_user",
            password="readonly_password"
        )
        cur = conn.cursor()
        cur.execute(sql_query)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        cur.close()
        conn.close()
        return rows, columns, None
    except Exception as e:
        return None, None, str(e)

# === ГЕНЕРАЦИЯ РЕЗЮМЕ ===
def generate_summary(user_query: str, sql: str, results, columns):
    results_str = str(results[:5]) if results else "Нет данных"
    prompt_text = f"""Ты аналитик данных. Дай краткое резюме результата SQL-запроса СТРОГО НА РУССКОМ ЯЗЫКЕ.

Запрос пользователя: {user_query}
SQL-запрос: {sql}
Колонки результата: {columns}
Результат (первые строки): {results_str}

Напиши 1-2 предложения на русском языке, объясняющие результат. Например:
- "Общая выручка за все время составляет 1 234 567 рублей."
- "Топ-3 исполнителя по продажам: AC/DC (150 продаж), Metallica (120 продаж), U2 (100 продаж)."

Резюме на русском:"""
    
    try:
        response = llm.invoke(prompt_text)
        return response.content.strip()
    except Exception:
        return f"Получено {len(results) if results else 0} строк результата."

# === ИНТЕРФЕЙС ===
st.markdown("### Задайте вопрос о данных:")
user_query = st.text_input("Например: Покажи топ-3 исполнителей по количеству продаж", "")

if st.button("Выполнить запрос"):
    if not user_query.strip():
        st.warning("Пожалуйста, введите запрос.")
    else:
        with st.spinner("Агент генерирует SQL-запрос..."):
            try:
                # ПРОВЕРКА 1: Намерение пользователя
                is_dangerous, intent_reason = check_user_intent(user_query)
                
                if is_dangerous:
                    st.error("[Безопасность] Обнаружено намерение выполнить деструктивную операцию!")
                    st.warning(f"Причина: {intent_reason}")
                    st.info("Агент работает только в режиме READ-ONLY. Операции INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE запрещены.")
                else:
                    # Генерация SQL
                    sql_query = generate_sql(user_query)
                    
                    if not sql_query:
                        st.warning("Не удалось сгенерировать SQL-запрос. Попробуйте перефразировать вопрос.")
                    else:
                        # Показываем SQL
                        st.markdown("#### Сгенерированный SQL:")
                        st.code(sql_query, language="sql")
                        
                        # ПРОВЕРКА 2: Безопасность SQL
                        is_safe, safety_error = validate_sql_safety(sql_query)
                        
                        if not is_safe:
                            st.error("[Безопасность] SQL-запрос заблокирован валидатором.")
                            st.warning(safety_error)
                        else:
                            # Выполнение SQL
                            with st.spinner("Выполнение запроса..."):
                                rows, columns, error = execute_sql(sql_query)
                            
                            if error:
                                st.error(f"Ошибка выполнения SQL: {error}")
                                st.info("Совет: Попробуйте перефразировать запрос.")
                            else:
                                st.success("Запрос выполнен успешно!")
                                
                                # Показываем результаты
                                st.markdown("#### Результаты:")
                                if rows:
                                    df = pd.DataFrame(rows, columns=columns)
                                    st.dataframe(df, use_container_width=True)
                                    
                                    # Генерация резюме
                                    with st.spinner("Формирование резюме..."):
                                        summary = generate_summary(user_query, sql_query, rows, columns)
                                        st.markdown("#### Резюме:")
                                        st.info(summary)
                                else:
                                    st.info("Запрос не вернул результатов.")
                    
            except Exception as e:
                st.error(f"Ошибка: {str(e)}")
                st.info("Совет: Попробуйте перефразировать запрос.")

# === ПРИМЕРЫ ===
st.markdown("---")
st.markdown("### Примеры запросов:")
examples = [
    "Покажи общую выручку за все время",
    "Какая страна принесла больше всего денег?",
    "Покажи топ-5 исполнителей по количеству продаж",
    "Какой жанр музыки самый популярный?",
    "Покажи список треков, которые покупали клиенты из USA",
    "Сколько клиентов зарегистрировано в каждой стране?",
    "Удали всех клиентов из Канады"
]
for ex in examples:
    st.markdown(f"- *{ex}*")
