from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import json
import random

app = FastAPI()

# Hiermee kan de browser de HTML-bestanden vinden in de 'static' map
app.mount("/static", StaticFiles(directory="static"), name="static")

# Laad de kaarten uit het JSON-bestand
def load_cards():
    with open("cards.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

cards_data = load_cards()

# Helper functies voor kaarten
def get_random_black_card():
    """Kies een willekeurige zwarte kaart en format deze"""
    black_card = random.choice(cards_data["blacks"])
    # Join de tekst segmenten, lege strings worden "___"
    formatted = ""
    for segment in black_card:
        if segment == "":
            formatted += "___"
        else:
            formatted += segment
    return formatted

def get_random_white_cards(count=5):
    """Kies willekeurige witte kaarten"""
    white_cards = random.sample(cards_data["whites"], min(count, len(cards_data["whites"])))
    # Extract de tekst uit elke kaart (zit in een array)
    return [card[0] if isinstance(card, list) else card for card in white_cards]

# Beheer van actieve verbindingen
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# Spel status
game_state = {
    "current_black_card": None,
    "players": {},
    "round_in_progress": False
}

def start_new_round():
    """Start een nieuwe ronde met een nieuwe zwarte kaart"""
    game_state["current_black_card"] = get_random_black_card()
    game_state["round_in_progress"] = True
    # Geef elke speler nieuwe witte kaarten
    for player_id in game_state["players"]:
        game_state["players"][player_id]["hand"] = get_random_white_cards(5)
    return game_state["current_black_card"]

@app.get("/")
async def get():
    # Stuurt de gebruiker naar de frontend
    with open("static/index.html") as f:
        return HTMLResponse(f.read())

@app.get("/api/cards/black")
async def get_black_card():
    """Krijg een willekeurige zwarte kaart"""
    return {"card": get_random_black_card()}

@app.get("/api/cards/white")
async def get_white_cards(count: int = 5):
    """Krijg willekeurige witte kaarten"""
    return {"cards": get_random_white_cards(count)}

@app.get("/api/game/state")
async def get_game_state():
    """Krijg de huidige spel status"""
    return {
        "black_card": game_state["current_black_card"],
        "player_count": len(game_state["players"]),
        "round_in_progress": game_state["round_in_progress"]
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    player_id = str(id(websocket))
    game_state["players"][player_id] = {"hand": get_random_white_cards(5)}
    
    try:
        # Stuur de huidige zwarte kaart en hand naar de nieuwe speler
        if game_state["current_black_card"]:
            await websocket.send_text(json.dumps({
                "type": "game_state",
                "black_card": game_state["current_black_card"],
                "hand": game_state["players"][player_id]["hand"]
            }))
        
        await manager.broadcast(json.dumps({
            "type": "player_joined",
            "message": f"Nieuwe speler! Totaal: {len(game_state['players'])} spelers"
        }))
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "play_card":
                # Speler speelt een kaart
                card_text = message.get("card")
                await manager.broadcast(json.dumps({
                    "type": "card_played",
                    "card": card_text,
                    "player": player_id
                }))
            
            elif message.get("type") == "new_round":
                # Start een nieuwe ronde
                black_card = start_new_round()
                # Stuur elke speler hun nieuwe hand
                for conn in manager.active_connections:
                    pid = str(id(conn))
                    if pid in game_state["players"]:
                        await conn.send_text(json.dumps({
                            "type": "new_round",
                            "black_card": black_card,
                            "hand": game_state["players"][pid]["hand"]
                        }))
            
            elif message.get("type") == "get_hand":
                # Stuur de speler hun huidige hand
                await websocket.send_text(json.dumps({
                    "type": "hand",
                    "hand": game_state["players"][player_id]["hand"]
                }))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        if player_id in game_state["players"]:
            del game_state["players"][player_id]
        await manager.broadcast(json.dumps({
            "type": "player_left",
            "message": f"Een speler heeft de kamer verlaten. Totaal: {len(game_state['players'])} spelers"
        }))