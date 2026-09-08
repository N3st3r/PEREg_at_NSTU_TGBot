import asyncio
from contextlib import asynccontextmanager
import uvicorn

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from fastapi.middleware.cors import CORSMiddleware

from nstu_auth import get_nstu_tokens_auto, DEFAULT_API_KEY

BOT_TOKEN = "Вставить свой токен полученный от @BotFather"
WEBAPP_URL = "https:// Вставить ссылку на сайт, в котором хостится MiniApp/находится index.html"

user_sessions = {}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BookRequest(BaseModel):
    tg_id: int
    day_number: int
    teacher_id: int
    pair_number: int
    section_id: int

class CancelRequest(BaseModel):
    registration_id: int | None = None

@dp.message(CommandStart())
@dp.message(Command("login"))
async def login_cmd(message: Message):
    args = message.text.split(maxsplit=2)

    if len(args) < 3:
        await message.answer(
            "Для входа в приложение для записи на физкультуру нужно отправить команду формата:\n"
            "`/login логин пароль от ЛК НГТУ`",
            parse_mode="Markdown"
        )
        return

    username, password = args[1], args[2]

    try:
        await message.delete()
    except Exception:
        pass

    msg = await message.answer("Выполняю вход в личный кабинет НГТУ...")

    auth_data = await get_nstu_tokens_auto(username, password)

    if auth_data and auth_data.get("id_card"):
        user_sessions[message.from_user.id] = auth_data
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть запись ФЗК", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
        await msg.edit_text("Авторизация успешна! Нажмите кнопку ниже:", reply_markup=kb)
    else:
        await msg.edit_text("Не удалось авторизоваться. Проверьте правильность логина и пароля.")

@app.get("/")
async def render_webapp():
    return FileResponse("frontend/index.html")

@app.get("/api/sections")
async def get_sections(tg_id: int = Query(...)):
    session_data = user_sessions.get(tg_id)
    if not session_data:
        return JSONResponse(status_code=401, content={"error": "Сессия не найдена. Пройдите /login в боте."})

    id_card = session_data.get("id_card")
    api_key = session_data.get("api_key") or DEFAULT_API_KEY

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "ApiKey": api_key
    }
    if session_data.get("bearer_token"):
        headers["Authorization"] = f"Bearer {session_data['bearer_token']}"

    cookies = session_data.get("cookies", {})

    async with aiohttp.ClientSession(cookies=cookies) as session:
        try:
            sections_data = {}

            async with session.get(
                    "https://api.ciu.nstu.ru/v2.0/student/get_data/app/get_user_info?disableNotification=true",
                    headers=headers) as resp_user:
                if resp_user.status == 200:
                    sections_data["USER_INFO"] = await resp_user.json()
                else:
                    sections_data["USER_INFO"] = None

            async with session.get(
                    f"https://api.ciu.nstu.ru/v2.0/physcult/get_sections?id_card={id_card}&disableNotification=true",
                    headers=headers) as resp_sec:
                if resp_sec.status == 200:
                    data = await resp_sec.json()
                    if isinstance(data, dict):
                        sections_data.update(data)
                    elif isinstance(data, list):
                        sections_data["sections"] = data

            async with session.get(
                    f"https://api.ciu.nstu.ru/v2.0/physcult/get_student_choice?id_card={id_card}&disableNotification=true",
                    headers=headers) as resp_choice:
                if resp_choice.status == 200:
                    choice_data = await resp_choice.json()

                    extracted_reg = None
                    if isinstance(choice_data, dict):
                        if "CHOICE" in choice_data and isinstance(choice_data["CHOICE"], list) and len(choice_data["CHOICE"]) > 0:
                            extracted_reg = choice_data["CHOICE"][0]
                        elif "data" in choice_data and isinstance(choice_data["data"], list) and len(choice_data["data"]) > 0:
                            extracted_reg = choice_data["data"][0]
                    elif isinstance(choice_data, list) and len(choice_data) > 0:
                        extracted_reg = choice_data[0]

                    if extracted_reg:
                        sections_data["MY_REGISTRATION"] = extracted_reg
                        reg_id = (
                                extracted_reg.get("ID") or
                                extracted_reg.get("id") or
                                extracted_reg.get("REGISTRATION_ID") or
                                extracted_reg.get("registration_id") or
                                (extracted_reg.get("SECTION") and extracted_reg["SECTION"].get("ID"))
                        )
                        user_sessions[tg_id]["registration_id"] = reg_id
                    else:
                        sections_data["MY_REGISTRATION"] = None
                        user_sessions[tg_id]["registration_id"] = None

            return sections_data
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/cancel")
async def cancel_booking(tg_id: int = Query(...), req: CancelRequest | None = None):
    try:
        session_data = user_sessions.get(tg_id)
        if not session_data:
            return JSONResponse(status_code=400, content={"error": "Сессия не найдена"})

        reg_id = (req.registration_id if req and req.registration_id else None) or session_data.get("registration_id")

        if not reg_id:
            return JSONResponse(status_code=400, content={"error": "Не указан ID записи для отмены"})

        payload = {"registrationIds": [int(reg_id)]}
        api_key = session_data.get("api_key") or DEFAULT_API_KEY

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "ApiKey": api_key,
            "X-Apikey": api_key
        }
        if session_data.get("bearer_token"):
            headers["Authorization"] = f"Bearer {session_data['bearer_token']}"

        cookies = session_data.get("cookies", {})

        async with aiohttp.ClientSession(cookies=cookies) as session:
            url = "https://api.ciu.nstu.ru/v2.0/physcult/cancel_registration?disableNotification=true"

            async with session.post(url, headers=headers, json=payload) as resp:
                response_text = await resp.text()

                if resp.status != 200:
                    return JSONResponse(status_code=resp.status,
                                        content={"error": f"API НГТУ вернул статус {resp.status}: {response_text}"})

                try:
                    return await resp.json()
                except Exception:
                    return {"status": "ok", "raw": response_text}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {str(e)}"})

@app.post("/api/book")
async def book_section(req: BookRequest):
    session_data = user_sessions.get(req.tg_id)
    if not session_data:
        return JSONResponse(status_code=401, content={"error": "Сессия не найдена"})

    if not all([req.day_number, req.teacher_id, req.pair_number, req.section_id]):
        return JSONResponse(
            status_code=400,
            content={"error": "Не все параметры выбраны (секция, день, преподаватель, пара)."}
        )

    id_card = session_data.get("id_card")
    api_key = session_data.get("api_key") or DEFAULT_API_KEY

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Apikey": api_key
    }
    if session_data.get("bearer_token"):
        headers["Authorization"] = f"Bearer {session_data['bearer_token']}"

    cookies = session_data.get("cookies", {})

    payload = {
        "CHOICE": [{
            "DAY_NUMBER": req.day_number,
            "LESSON_NUMBER": req.pair_number,
            "SECTION_ID": req.section_id,
            "TEACHER_ID_CARD": req.teacher_id
        }],
        "ID_CARD": int(id_card)
    }

    async with aiohttp.ClientSession(cookies=cookies) as session:
        async with session.post(
            "https://api.ciu.nstu.ru/v2.0/physcult/register_for_section?disableNotification=true",
            json=payload,
            headers=headers
        ) as resp:
            try:
                data = await resp.json()
                return JSONResponse(status_code=resp.status, content=data)
            except Exception:
                text = await resp.text()
                return JSONResponse(status_code=resp.status, content={"status_code": resp.status, "text": text})


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)