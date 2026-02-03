#!/usr/bin/env python3
"""
Тест генерации изображений через Pollinations API
"""
import asyncio
import aiohttp
from urllib.parse import quote

async def test_pollinations():
    print("🧪 Тестирование Pollinations API...")
    print("=" * 50)
    
    prompts = [
        "simple test image",
        "tech illustration AI brain neon blue",
        "futuristic robot digital art"
    ]
    
    for i, prompt in enumerate(prompts, 1):
        print(f"\n📝 Тест {i}: {prompt}")
        
        url = f"https://image.pollinations.ai/prompt/{quote(prompt)}?width=512&height=512&nologo=true&seed={i}"
        print(f"🔗 URL: {url[:70]}...")
        
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=timeout) as resp:
                    print(f"📊 HTTP статус: {resp.status}")
                    print(f"📊 Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
                    
                    if resp.status != 200:
                        print(f"❌ Ошибка: HTTP {resp.status}")
                        continue
                    
                    data = await resp.read()
                    print(f"📦 Размер: {len(data)} байт ({len(data)//1024} KB)")
                    
                    # Проверка формата
                    if data[:3] == b'\xff\xd8\xff':
                        print("✅ Формат: JPEG")
                    elif data[:8] == b'\x89PNG\r\n\x1a\n':
                        print("✅ Формат: PNG")
                    else:
                        print(f"⚠️ Неизвестный формат: {data[:20]}")
                    
                    if len(data) > 5000:
                        fname = f"test_{i}.jpg"
                        with open(fname, "wb") as f:
                            f.write(data)
                        print(f"💾 Сохранено: {fname}")
                    else:
                        print("⚠️ Файл слишком маленький")
                        
        except asyncio.TimeoutError:
            print("❌ Таймаут!")
        except Exception as e:
            print(f"❌ Ошибка: {type(e).__name__}: {e}")
    
    print("\n" + "=" * 50)
    print("🏁 Тест завершён")

if __name__ == "__main__":
    asyncio.run(test_pollinations())
