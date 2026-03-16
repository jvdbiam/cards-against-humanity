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
    "players": {},  # player_id -> {name, hand, score}
    "player_order": [],  # List to track player order for consistent username assignment
    "round_in_progress": False,
    "submitted_cards": {},  # player_id -> card
    "card_to_player": {},  # card -> player_id (for scoring)
    "revealed": False,
    "votes": {}  # player_id -> card_index (who voted for which card)
}

def start_new_round():
    """Start een nieuwe ronde met een nieuwe zwarte kaart"""
    game_state["current_black_card"] = get_random_black_card()
    game_state["round_in_progress"] = True
    game_state["submitted_cards"] = {}
    game_state["card_to_player"] = {}
    game_state["revealed"] = False
    game_state["votes"] = {}
    # Geef elke speler nieuwe witte kaarten
    for player_id in game_state["players"]:
        game_state["players"][player_id]["hand"] = get_random_white_cards(5)
    return game_state["current_black_card"]

def get_scoreboard():
    """Get the current scoreboard"""
    scoreboard = []
    for player_id in game_state["player_order"]:
        if player_id in game_state["players"]:
            player = game_state["players"][player_id]
            scoreboard.append({
                "id": player_id,
                "name": player["name"],
                "score": player["score"]
            })
    return scoreboard

def all_players_submitted():
    """Check of alle spelers hun kaart hebben ingediend"""
    if len(game_state["players"]) == 0:
        return False
    return len(game_state["submitted_cards"]) == len(game_state["players"])

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
    player_number = len(game_state["player_order"]) + 1
    player_name = f"Speler {player_number}"
    
    game_state["players"][player_id] = {
        "hand": get_random_white_cards(5),
        "name": player_name,
        "score": 0
    }
    game_state["player_order"].append(player_id)
    
    try:
        # Stuur de huidige zwarte kaart en hand naar de nieuwe speler
        initial_data = {
            "type": "game_state",
            "player_id": player_id,
            "player_name": player_name,
            "player_count": len(game_state["players"]),
            "scoreboard": get_scoreboard(),
            "submitted_count": len(game_state["submitted_cards"])
        }
        if game_state["current_black_card"]:
            initial_data["black_card"] = game_state["current_black_card"]
            initial_data["hand"] = game_state["players"][player_id]["hand"]
            initial_data["revealed"] = game_state["revealed"]
            initial_data["submitted_cards"] = list(game_state["submitted_cards"].values()) if game_state["revealed"] else []
        
        await websocket.send_text(json.dumps(initial_data))
        
        await manager.broadcast(json.dumps({
            "type": "player_joined",
            "message": f"Nieuwe speler ({player_name}) is toegetreden!",
            "player_count": len(game_state["players"]),
            "submitted_count": len(game_state["submitted_cards"]),
            "scoreboard": get_scoreboard()
        }))
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "play_card":
                # Speler dient een kaart in
                card_text = message.get("card")
                
                # Check of speler al een kaart heeft ingediend
                if player_id in game_state["submitted_cards"]:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Je hebt al een kaart ingediend!"
                    }))
                    continue
                
                # Sla de ingediende kaart op
                game_state["submitted_cards"][player_id] = card_text
                
                # Verwijder de kaart uit de hand van de speler
                if card_text in game_state["players"][player_id]["hand"]:
                    game_state["players"][player_id]["hand"].remove(card_text)
                
                # Update iedereen over de voortgang
                await manager.broadcast(json.dumps({
                    "type": "card_submitted",
                    "submitted_count": len(game_state["submitted_cards"]),
                    "player_count": len(game_state["players"]),
                    "scoreboard": get_scoreboard()
                }))
                
                # Check of iedereen heeft ingediend
                if all_players_submitted():
                    game_state["revealed"] = True
                    # Shuffle and reveal alle kaarten
                    shuffled_cards = list(game_state["submitted_cards"].values())
                    random.shuffle(shuffled_cards)
                    # Create card info with player ids
                    card_info = []
                    for card in shuffled_cards:
                        # Find which player submitted this card
                        player_who_submitted = None
                        for pid, submitted_card in game_state["submitted_cards"].items():
                            if submitted_card == card:
                                player_who_submitted = pid
                                game_state["card_to_player"][card] = pid
                                break
                        card_info.append({
                            "card": card,
                            "player_id": player_who_submitted
                        })
                    await manager.broadcast(json.dumps({
                        "type": "reveal",
                        "cards": card_info
                    }))
            
            elif message.get("type") == "vote_card":
                # Speler stemt voor een kaart
                card_text = message.get("card")
                
                # Check if card was submitted by another player
                if card_text in game_state["card_to_player"]:
                    winner_id = game_state["card_to_player"][card_text]
                    # Prevent voting for your own card
                    if winner_id != player_id and player_id not in game_state["votes"]:
                        game_state["votes"][player_id] = card_text
                        
                        # Award point to the submitter
                        if winner_id in game_state["players"]:
                            game_state["players"][winner_id]["score"] += 1
                        
                        # Check if everyone has voted
                        if len(game_state["votes"]) == len(game_state["players"]) - 1:  # Everyone except the judge
                            # Find the winning card
                            vote_counts = {}
                            for voted_card in game_state["votes"].values():
                                vote_counts[voted_card] = vote_counts.get(voted_card, 0) + 1
                            
                            winning_card = max(vote_counts, key=vote_counts.get)
                            winning_player = game_state["card_to_player"].get(winning_card)
                            
                            await manager.broadcast(json.dumps({
                                "type": "round_end",
                                "winning_card": winning_card,
                                "winning_player": game_state["players"][winning_player]["name"] if winning_player in game_state["players"] else "Unknown",
                                "scoreboard": get_scoreboard()
                            }))
                    elif winner_id == player_id:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Je kunt niet op je eigen kaart stemmen!"
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
                            "hand": game_state["players"][pid]["hand"],
                            "submitted_count": 0,
                            "player_count": len(game_state["players"]),
                            "scoreboard": get_scoreboard()
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
        if player_id in game_state["player_order"]:
            game_state["player_order"].remove(player_id)
        if player_id in game_state["submitted_cards"]:
            del game_state["submitted_cards"][player_id]
        if player_id in game_state["votes"]:
            del game_state["votes"][player_id]
        await manager.broadcast(json.dumps({
            "type": "player_left",
            "message": f"Een speler heeft de kamer verlaten. Totaal: {len(game_state['players'])} spelers",
            "player_count": len(game_state["players"]),
            "submitted_count": len(game_state["submitted_cards"]),
            "scoreboard": get_scoreboard()
        }))