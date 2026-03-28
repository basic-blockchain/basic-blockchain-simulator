# Simulador Básico de Blockchain

Este proyecto implementa un simulador educativo de blockchain en Python usando Flask. Permite minar bloques, consultar la cadena y validar su integridad a través de una API REST local.

## Archivos principales

- `basic-blockchain.py`: Script principal que implementa la lógica de la blockchain y expone los endpoints vía Flask.
- `blockchain-simulator-guide.md`: Guía detallada de uso, ejemplos de endpoints y conceptos clave.

## Requisitos

- Python 3.x
- Flask (`pip install flask`)

## Uso rápido

1. Instala Flask:
   ```bash
   pip install flask
   ```
2. Ejecuta el servidor:
   ```bash
   python basic-blockchain.py
   ```
3. Accede a los endpoints desde tu navegador o herramientas como curl/postman:
   - `GET /` — Estado y rutas disponibles
   - `GET /get_chain` — Ver la cadena de bloques
   - `GET /mine_block` — Minar un nuevo bloque
   - `GET /valid` — Validar integridad de la cadena

## Ejemplo de endpoints

```bash
curl http://127.0.0.1:5000/
curl http://127.0.0.1:5000/get_chain
curl http://127.0.0.1:5000/mine_block
curl http://127.0.0.1:5000/valid
```

## Conceptos clave

- **Bloque génesis**: Primer bloque de la cadena, creado automáticamente.
- **Proof of Work**: Algoritmo que busca un número cuyo hash empiece con `00000`.
- **Hash**: Huella digital SHA-256 de cada bloque.
- **previous_hash**: Enlace entre bloques para garantizar la inmutabilidad.

## Referencias

Consulta `blockchain-simulator-guide.md` para una guía completa, ejemplos en Python y explicaciones detalladas.
