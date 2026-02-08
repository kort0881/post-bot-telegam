import sqlite3
import re

# Экономические термины для поиска
ECON_TERMS = [
    "инфляция", "федеральная резервная система", "фрс", "процентная ставка",
    "рецессия", "ввп", "безработица", "экономический рост", "тарифы",
    "фондовый рынок", "nasdaq", "dow jones", "s&p 500", "облигации",
    "inflation", "federal reserve", "fed", "interest rate", "recession",
    "gdp", "unemployment", "economic growth", "stock market", "bonds",
    "центральный банк", "валюта", "бюджетный дефицит", "бостик"
]

def clean_economics_posts():
    """Удаляет посты про экономику из БД"""
    conn = sqlite3.connect("posted_articles.db")
    cursor = conn.cursor()
    
    # Получаем все посты
    cursor.execute("SELECT id, title, summary FROM posted_articles")
    all_posts = cursor.fetchall()
    
    deleted_count = 0
    
    for post_id, title, summary in all_posts:
        text = f"{title} {summary}".lower()
        
        # Проверяем на экономические термины
        econ_count = sum(1 for term in ECON_TERMS if term in text)
        
        # Если 2+ экономических термина — удаляем
        if econ_count >= 2:
            # Проверяем, есть ли AI-контекст
            ai_keywords = ["ai", "artificial intelligence", "machine learning", "нейро", "ии"]
            has_ai = any(kw in text for kw in ai_keywords)
            
            if not has_ai:
                cursor.execute("DELETE FROM posted_articles WHERE id = ?", (post_id,))
                deleted_count += 1
                print(f"🗑️ Удалён: {title[:60]}...")
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Удалено {deleted_count} экономических постов")

if __name__ == "__main__":
    clean_economics_posts()
