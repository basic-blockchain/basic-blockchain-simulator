# Blockchain Simulator — Guía de uso

Simulador de cadena de bloques construido con Python y Flask.
Expone una API REST local para minar bloques, consultar la cadena y verificar su integridad.

---

## Requisitos

- Python 3.x
- Flask instalado:
  ```bash
  pip install flask
  ```

---

## Iniciar el servidor

```bash
python basic-blockchain.py
```

El servidor queda disponible en `http://127.0.0.1:5000`.

> Cada vez que se modifique el script, detener con `Ctrl+C` y volver a ejecutar.

---

## Endpoints

### `GET /`
Muestra el estado del servidor y las rutas disponibles.

**Request:**
```
GET http://127.0.0.1:5000/
```

**Respuesta:**
```json
{
  "message": "Blockchain simulator is running",
  "routes": {
    "get_chain": "/get_chain",
    "mine_block": "/mine_block",
    "valid": "/valid"
  }
}
```

---

### `GET /get_chain`
Devuelve todos los bloques de la cadena y su longitud actual.

**Request:**
```
GET http://127.0.0.1:5000/get_chain
```

**Respuesta:**
```json
{
  "chain": [
    {
      "index": 1,
      "previous_hash": "0",
      "proof": 1,
      "timestamp": "2026-03-26 20:00:00.000000"
    }
  ],
  "length": 1
}
```

> El primer bloque (índice 1) es el **bloque génesis**, creado automáticamente al iniciar el servidor.

---

### `GET /mine_block`
Ejecuta el algoritmo Proof of Work y agrega un nuevo bloque a la cadena.

**Request:**
```
GET http://127.0.0.1:5000/mine_block
```

**Respuesta:**
```json
{
  "index": 2,
  "message": "A block is MINED",
  "previous_hash": "a3f1c9...d84b",
  "proof": 533,
  "timestamp": "2026-03-26 20:05:00.000000"
}
```

> Cada llamada agrega un bloque nuevo. El campo `previous_hash` encadena cada bloque con el anterior.

---

### `GET /valid`
Verifica que todos los bloques de la cadena sean íntegros y no hayan sido alterados.

**Request:**
```
GET http://127.0.0.1:5000/valid
```

**Respuesta (cadena válida):**
```json
{
  "message": "The Blockchain is valid."
}
```

**Respuesta (cadena alterada):**
```json
{
  "message": "The Blockchain is not valid."
}
```

---

## Flujo del simulacro

| Paso | Acción | Endpoint |
|------|--------|----------|
| 1 | Verificar que el servidor responde | `GET /` |
| 2 | Ver el bloque génesis inicial | `GET /get_chain` |
| 3 | Minar el bloque 2 | `GET /mine_block` |
| 4 | Minar el bloque 3 | `GET /mine_block` |
| 5 | Ver la cadena con 3 bloques | `GET /get_chain` |
| 6 | Validar integridad de la cadena | `GET /valid` |

---

## Ejemplo completo con Python

```python
import requests

base = "http://127.0.0.1:5000"

# 1. Estado del servidor
print(requests.get(f"{base}/").json())

# 2. Ver cadena inicial
print(requests.get(f"{base}/get_chain").json())

# 3. Minar 3 bloques
for i in range(3):
    print(requests.get(f"{base}/mine_block").json())

# 4. Ver cadena completa
print(requests.get(f"{base}/get_chain").json())

# 5. Validar
print(requests.get(f"{base}/valid").json())
```

---

## Ejemplo con curl

```bash
# Estado
curl http://127.0.0.1:5000/

# Ver cadena
curl http://127.0.0.1:5000/get_chain

# Minar bloque
curl http://127.0.0.1:5000/mine_block

# Validar cadena
curl http://127.0.0.1:5000/valid
```

---

## Conceptos clave

| Término | Descripción |
|---------|-------------|
| **Bloque génesis** | Primer bloque de la cadena, con `previous_hash = "0"` |
| **Proof of Work** | Algoritmo que busca un número cuyo hash empiece con `00000` |
| **Hash** | Huella digital SHA-256 de cada bloque |
| **previous_hash** | Enlace entre bloques — garantiza la inmutabilidad de la cadena |
