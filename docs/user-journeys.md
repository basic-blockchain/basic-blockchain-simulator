# Guía de Casos de Uso – Blockchain Simulator

## Objetivo

Esta guía describe los flujos completos de usuario a través de la plataforma, desde registro hasta operaciones avanzadas. Aplica a todos los roles: **VIEWER**, **OPERATOR**, **ADMIN**.

---

## 1. REGISTRO E INGRESO

### Caso 1.1: Registro de usuario (Público)

**Actor:** Visitante anónimo  
**Objetivo:** Crear cuenta y activarla

**Pasos:**

1. Navega a `/register` (pública, sin autenticación)
2. Ingresa:
   - `username` (único, 3-50 caracteres)
   - `email` (único, válido)
   - `display_name` (nombre completo, opcional)
3. Click **Register**
4. Sistema envía código de activación (mock: visible en modal)
5. Navega a `/activate`
6. Ingresa:
   - `username`
   - `activation_code` (recibido)
   - `password` (fuerte, ≥8 caracteres)
7. Click **Activate**
8. Cuenta activada con rol por defecto **VIEWER**
9. Redirige a `/login`

**Resultado:** Usuario puede ingresar con sus credenciales.

---

### Caso 1.2: Ingreso a la plataforma (Público)

**Actor:** Usuario registrado  
**Objetivo:** Obtener sesión y token JWT

**Pasos:**

1. Navega a `/login` (pública)
2. Ingresa `username` y `password`
3. Click **Login**
4. Sistema valida credenciales (no banned, password correcto)
5. Retorna `access_token` (JWT válido por 30 min)
6. Cliente guarda token en localStorage y sesión
7. Redirige a `/dashboard` o última ruta autenticada

**Precondiciones:**
- Cuenta activada
- Usuario no baneado
- Contraseña correcta

**Resultado:** Token JWT guardado, usuario autenticado para 30 minutos.

---

### Caso 1.3: Cambio de contraseña

**Actor:** Usuario autenticado  
**Objetivo:** Actualizar contraseña con validación

**Pasos:**

1. Accede a `/profile` o panel de usuario
2. Ingresa `current_password` y `new_password`
3. Click **Change Password**
4. Sistema valida contraseña actual contra hash
5. Hash nueva contraseña con bcrypt
6. Actualiza en DB
7. Retorna confirmación
8. Sesión se mantiene activa

**Precondiciones:**
- Usuario autenticado
- Contraseña actual correcta

**Resultado:** Próxima sesión requiere nueva contraseña.

---

## 2. GESTIÓN DE WALLETS (Todos los usuarios)

### Caso 2.1: Crear wallet personal

**Actor:** Usuario VIEWER, OPERATOR  
**Objetivo:** Crear wallet para recibir/enviar fondos

**Pasos:**

1. Navega a `/wallet`
2. Click **Create Wallet**
3. Selecciona moneda de lista activa (ej: NATIVE, BTC, ETH)
4. Click **Create**
5. Sistema:
   - Genera par de claves (privada/pública)
   - Crea entrada en DB con `user_id`, `currency`, `balance=0`
   - Retorna `wallet_id` y `public_key`
6. Muestra:
   - Wallet ID (copiar a portapapeles)
   - Public key (compartir)
   - Balance actual: 0
7. Wallet aparece en lista **My Wallets**

**Precondiciones:**
- Moneda activa en catálogo
- Usuario no baneado

**Resultado:** Wallet nueva lista para operaciones, balance en cero.

---

### Caso 2.2: Ver mis wallets y balances

**Actor:** Usuario autenticado  
**Objetivo:** Consultar monederos personales y saldos

**Pasos:**

1. Navega a `/wallet`
2. Tabla **My Wallets** muestra:
   - Wallet ID
   - Moneda
   - Balance (Decimal, con `decimals` correctos)
   - Tipo de wallet (USER, TREASURY)
   - Frozen (sí/no)
3. Click en wallet para detalles:
   - Transacciones recientes
   - UTXO si modelo UTXO
   - Public key para recibir fondos
4. Autorefresh cada 30 seg

**Precondiciones:**
- Usuario tiene al menos una wallet

**Resultado:** Vista clara del patrimonio en múltiples monedas.

---

### Caso 2.3: Transferencia entre wallets del mismo usuario

**Actor:** Usuario OPERATOR con 2+ wallets en misma moneda  
**Objetivo:** Mover fondos internamente

**Pasos:**

1. En `/wallet`, selecciona wallet origen (balance > 0)
2. Click **Transfer**
3. Ingresa:
   - Wallet destino (debe ser su otra wallet)
   - Monto (> 0, ≤ balance disponible)
   - Fee (si aplica, para minería)
4. Click **Confirm**
5. Sistema:
   - Valida permiso TRANSFER
   - Valida suficiente balance
   - Crea transacción firmada (si require)
   - Agrega a mempool
   - Registra en audit: `TRANSFER`
6. Transacción en estado PENDING
7. Al minar bloque: transacción se confirma, balances actualizan

**Precondiciones:**
- OPERATOR u ADMIN
- Origen y destino son sus wallets
- Saldo suficiente

**Resultado:** Fondos transferidos, audit registrado, balance actualizado en bloque siguiente.

---

### Caso 2.4: Transferencia entre usuarios (cross-user)

**Actor:** Usuario OPERATOR  
**Objetivo:** Enviar fondos a otro usuario

**Pasos:**

1. En `/wallet`, click **Send to Another User**
2. Ingresa:
   - Recipient username o user_id
   - Recipient wallet ID (valida que pertenezca a ese usuario)
   - Monto
   - Reference (opcional, ej: "Invoice #123")
3. Click **Preview** → muestra fee y monto neto
4. Click **Send**
5. Sistema:
   - Valida destinatario existe y no está baneado
   - Valida wallet destino es del destinatario
   - Crea transacción con `sender`, `receiver`, `amount`
   - Si cross-currency: aplica tasa de conversión
   - Registra en audit: `TRANSFER` con recipient
6. Transacción en PENDING
7. Destinatario ve entrada en mempool en tiempo real (WebSocket)
8. Al minar: transacción confirma, balance actualizado

**Precondiciones:**
- OPERATOR o ADMIN
- Saldo suficiente
- Destinatario activo (no baneado)
- Wallets en misma moneda (o tasa exchange configurada)

**Resultado:** Fondos enviados, ambos usuarios ven en audit.

---

### Caso 2.5: Intercambio de monedas (Exchange)

**Actor:** Usuario OPERATOR con wallets en 2 monedas distintas  
**Objetivo:** Convertir fondos a otra moneda usando tasa de cambio

**Pasos:**

1. En `/wallet`, click **Exchange**
2. Ingresa:
   - Wallet origen (BTC) con balance > 0
   - Wallet destino (ETH, diferente moneda)
   - Monto a convertir
3. Sistema consulta tasa BTC→ETH en DB
4. Calcula:
   - `gross = amount * rate`
   - `commission = gross * fee_rate`
   - `net = gross - commission`
5. Muestra preview
6. Click **Confirm Exchange**
7. Sistema:
   - Crea transacción tipo EXCHANGE
   - Debita monto de origen
   - Acredita neto en destino
   - Registra fee en treasury (si configurado)
   - Audit: `TRANSFER` con metadatos exchange
8. Al minar: exchange ejecutado, balances finales

**Precondiciones:**
- OPERATOR o ADMIN
- Tasa cambio configurada (admin)
- Saldo suficiente en origen
- Wallets de usuario en monedas diferentes
- Mismo modelo de wallet (no cross-model)

**Resultado:** Fondos intercambiados, comisión pagada, balances actualizados.

---

## 3. GESTIÓN DE BLOCKCHAIN (Blockchain operators)

### Caso 3.1: Minar un bloque

**Actor:** OPERATOR  
**Objetivo:** Resolver PoW y crear bloque confirmando transacciones

**Pasos:**

1. Navega a `/chain` o dashboard
2. Click **Mine Block**
3. Sistema:
   - Toma última transacción del mempool
   - Incrementa nonce hasta encontrar proof de trabajo
   - Crea bloque con:
     - `proof`, `previous_hash`, `merkle_root`
     - Transacciones incluidas
     - Timestamp
   - Aplica deltas de balance a wallets
   - Persiste en BD
4. Broadcast en WebSocket: `event: block_mined`
5. Tabla **Recent Blocks** actualiza
6. Mempool limpia (txs que fueron en bloque)
7. Rate-limit: máx 5 bloques/minuto por usuario

**Precondiciones:**
- OPERATOR o ADMIN
- Mempool no vacío (o al menos minería es válida)

**Resultado:** Bloque nuevo, transacciones confirmadas, auditoría registrada.

---

### Caso 3.2: Ver cadena de bloques

**Actor:** Cualquiera (pública)  
**Objetivo:** Consultar cadena completa

**Pasos:**

1. Navega a `/chain`
2. Tabla de bloques muestra:
   - Índice
   - Timestamp
   - Proof
   - Previous hash
   - Merkle root
   - # Transacciones
3. Click en bloque: detalles de txs incluidas
4. Click en tx: datos completos (sender, receiver, amount, fee)
5. Endpoint `/api/v1/chain` retorna array de bloques

**Precondiciones:**
- Ninguna (pública)

**Resultado:** Transparencia total de la cadena.

---

### Caso 3.3: Validar integridad de cadena

**Actor:** Auditor (pública)  
**Objetivo:** Verificar que cadena es válida (hashes, PoW)

**Pasos:**

1. Navega a `/validation`
2. Click **Validate Chain**
3. Sistema:
   - Itera cada bloque
   - Verifica `previous_hash` es el hash del anterior
   - Verifica `proof` cumple dificultad
   - Verifica merkle root de txs
4. Retorna:
   - ✓ Valid si todo correcto
   - ✗ Invalid con bloque problemático si falla
5. Muestra timestmp de validación

**Precondiciones:**
- Cadena con 1+ bloques

**Resultado:** Confirmación de integridad o identificación de adulteración.

---

### Caso 3.4: Ver mempool

**Actor:** OPERATOR, VIEWER (pública)  
**Objetivo:** Consultar transacciones pendientes

**Pasos:**

1. Navega a `/mempool`
2. Tabla **Pending Transactions** muestra:
   - Sender
   - Receiver
   - Amount
   - Fee
   - Timestamp ingreso
   - Estado (PENDING)
3. Ordena por timestamp o fee (descending)
4. Filter por:
   - Sender
   - Receiver
   - Moneda
5. Autorefresh en tiempo real (WebSocket)

**Precondiciones:**
- Ninguna (pública)

**Resultado:** Visibilidad de transacciones no confirmadas.

---

### Caso 3.5: Resolver consenso (chain resolution)

**Actor:** OPERATOR, nodo distribuido  
**Objetivo:** Sincronizar con cadena más larga en red

**Pasos:**

1. Navega a `/nodes`
2. Click **Resolve Consensus**
3. Sistema:
   - Consulta todos nodos registrados
   - Descarga sus cadenas
   - Compara longitud y validez
   - Si existe cadena válida más larga: la adopta
   - Reemplaza cadena local si necesario
   - Auditaría evento `CONSENSUS_RESOLVED`
4. Retorna:
   - `replaced: true/false`
   - Nueva longitud
5. Mempool se reinicia (txs no confirmadas reencoladas si necesario)

**Precondiciones:**
- 2+ nodos registrados
- Red simulada o distributed

**Resultado:** Cadena sincronizada con red, cadena local puede actualizarse.

---

## 4. ADMINISTRACIÓN (ADMIN only)

### Caso 4.1: Crear moneda

**Actor:** ADMIN  
**Objetivo:** Agregar nueva moneda al catálogo

**Pasos:**

1. Navega a `/admin/currencies`
2. Sección **Create Currency**, ingresa:
   - Code (USD, EUR, BTC, 3-10 caracteres, sin espacios)
   - Name ("US Dollar", "Bitcoin")
   - Decimals (0-18, típicamente 8)
   - Active (sí/no)
3. Click **Create**
4. Sistema:
   - Valida código único
   - Valida decimals rango
   - Inserta en DB
   - Audit: `ACTION_CURRENCY_CREATED`
5. Moneda aparece en catálogo
   - Usuarios pueden crear wallets con ella
   - Solo si `active=true`

**Precondiciones:**
- ADMIN

**Resultado:** Moneda nueva disponible para wallets y exchange.

---

### Caso 4.2: Crear wallet de tesorería

**Actor:** ADMIN  
**Objetivo:** Crear wallet central para fondeo de usuarios

**Pasos:**

1. Navega a `/admin/treasury`
2. Click **Create Treasury Wallet**
3. Selecciona moneda (debe estar activa)
4. Click **Create**
5. Sistema:
   - Valida no existe treasury de esa moneda ya
   - Crea wallet con `wallet_type=TREASURY`
   - Owner = SYSTEM user
   - Balance inicializado en 0
   - Retorna wallet_id, public_key
6. Audit: `ACTION_TREASURY_WALLET_CREATED`
7. Wallet lista en `/admin/treasury`

**Precondiciones:**
- ADMIN
- Moneda activa
- No existe treasury de esa moneda

**Resultado:** Treasury wallet para operaciones de financiamiento.

---

### Caso 4.3: Mint (Acuñar fondos en tesorería)

**Actor:** ADMIN (MINT permission)  
**Objetivo:** Acuñar fondos en treasury para fondeo

**Pasos:**

1. En `/admin/wallets`
2. Localiza treasury wallet
3. Click **Mint**
4. Ingresa monto
5. Click **Confirm Mint**
6. Sistema:
   - Crea transacción especial MINT (sin sender)
   - Acredita monto a treasury
   - Audit: `ACTION_WALLET_MINT`
7. Balance de treasury aumenta inmediatamente
8. Fondos disponibles para top-ups

**Precondiciones:**
- ADMIN con permiso MINT
- Treasury wallet existe

**Resultado:** Fondos virtuales acuñados, balance treasury aumentado.

---

### Caso 4.4: Top-up de wallet de usuario

**Actor:** ADMIN  
**Objetivo:** Fondear wallet de usuario desde treasury

**Pasos:**

1. Navega a `/admin/wallets`
2. Selecciona wallet de usuario
3. Click **Top-Up**
4. Ingresa:
   - Treasury wallet (origen)
   - Monto
   - Reference (ej: "Welcome bonus")
5. Click **Confirm**
6. Sistema:
   - Debita treasury
   - Acredita wallet usuario
   - Crea transacción tipo TOP_UP
   - Audit: `ACTION_WALLET_TOP_UP`
7. Balance del usuario actualiza

**Precondiciones:**
- ADMIN con permiso TOP_UP
- Treasury suficiente balance
- Wallets en misma moneda

**Resultado:** Usuario fundea, patrimonio aumentado.

---

### Caso 4.5: Configurar tasas de cambio

**Actor:** ADMIN  
**Objetivo:** Establecer tasas manuales para intercambio

**Pasos:**

1. Navega a `/admin/exchange-rates`
2. Sección **Set Exchange Rate**, ingresa:
   - From Currency (ej: BTC)
   - To Currency (ej: ETH)
   - Rate (ej: 15.5)
   - Fee Rate (ej: 0.01 = 1% comisión)
3. Click **Save**
4. Sistema:
   - Valida currencies existen y son distintas
   - Valida rate > 0
   - Valida fee_rate 0-1
   - Inserta en DB con `source=MANUAL`
   - Audit: `ACTION_EXCHANGE_RATE_SET`
5. Tasa aparece en tabla
6. Usuarios pueden hacer exchange con esa tasa

**Precondiciones:**
- ADMIN
- Ambas monedas activas

**Resultado:** Tasa configurada para intercambios.

---

### Caso 4.6: Sincronizar tasas de cambio desde Binance

**Actor:** ADMIN  
**Objetivo:** Actualizar tasas en lote desde proveedor externo

**Pasos:**

1. Navega a `/admin/exchange-rates`
2. Sección **Sync Exchange Rates**, ingresa:
   - Provider: BINANCE o CRYPTO_COM
   - Pairs (CSV): "BTC/USDT,ETH/USDT,SOL/USDT"
3. Click **Sync Now**
4. Sistema:
   - Valida currencies en BD
   - Consulta Binance API para cada par
   - Obtiene precios reales
   - Inserta en DB con `source=BINANCE`
   - Audit: `ACTION_EXCHANGE_RATE_SET` con provider
5. Tabla actualiza con nuevas tasas
6. Usuarios usan tasas reales inmediatamente

**Precondiciones:**
- ADMIN
- Currencies activas en BD
- Binance API accesible
- Pares válidos en Binance (ej: BTCUSDT)

**Resultado:** Tasas reales en tiempo real, usuarios hacen exchange con precio de mercado.

---

### Caso 4.7: Configurar scheduler automático de tasas

**Actor:** DevOps / Sysadmin (via env vars)  
**Objetivo:** Sincronizar tasas cada N minutos automáticamente

**Pasos:**

1. En el servidor, edita `.env` o configura:
   ```
   EXCHANGE_FEED_ENABLED=true
   EXCHANGE_FEED_PROVIDER=BINANCE
   EXCHANGE_FEED_INTERVAL_SECONDS=300
   EXCHANGE_FEED_PAIRS=BTC/USDT,ETH/USDT
   ```
2. Reinicia app
3. Sistema:
   - Verifica config en @app.before_serving
   - Inicia background task de sincronización
   - Cada 300 seg (5 min):
     - Consulta Binance
     - Actualiza tasas en DB
     - Registra logs de éxito/error
   - Continúa indefinidamente hasta restart
4. ADMIN ve tasas siempre frescas en `/admin/exchange-rates`
5. Usuarios operan con precios reales automáticos

**Precondiciones:**
- EXCHANGE_FEED_ENABLED=true en env
- Valid provider y pairs

**Resultado:** Tasas sincronizadas automáticamente cada N segundos sin intervención manual.

---

### Caso 4.8: Gestionar usuarios

**Actor:** ADMIN  
**Objetivo:** Listar, actualizar, banear usuarios

**Pasos A: Listar usuarios**

1. Navega a `/admin/users`
2. Tabla de usuarios con:
   - User ID
   - Username
   - Display Name
   - Email
   - Roles
   - Banned (sí/no)
3. Filtro por: username, email, banned status
4. Ordena por columna

**Pasos B: Actualizar usuario**

1. Click en usuario → detalles
2. Click **Edit**
3. Modifica:
   - Display Name
   - Email
4. Click **Save**
5. Audit: `ACTION_USER_UPDATED`

**Pasos C: Banear usuario**

1. Click usuario → detalles
2. Click **Ban**
3. Confirma
4. Sistema:
   - Marca `banned=true`
   - Congela todas sus wallets automáticamente
   - Audit: `ACTION_USER_BANNED`
5. Usuario no puede loguear más (AUTH_INVALID_CREDENTIALS)

**Pasos D: Desbanear usuario**

1. Click usuario → detalles
2. Click **Unban**
3. Confirma
4. Sistema:
   - Marca `banned=false`
   - Opción: descongelar wallets
   - Audit: `ACTION_USER_UNBANNED`
5. Usuario puede loguear nuevamente

**Precondiciones:**
- ADMIN

**Resultado:** Control completo de ciclo de vida de usuario.

---

### Caso 4.9: Asignar y revocar roles

**Actor:** ADMIN  
**Objetivo:** Cambiar roles de usuario

**Pasos:**

1. Navega a `/admin/users`
2. Click usuario
3. Click **Roles**
4. Botones:
   - Grant OPERATOR, VIEWER, etc.
   - Revoke rol actual
5. Click Grant/Revoke
6. Confirma
7. Sistema:
   - Actualiza roles en DB
   - Audit: `ACTION_ROLE_GRANTED` o `ACTION_ROLE_REVOKED`
8. Usuario tiene nuevos permisos inmediatamente en próxima solicitud

**Precondiciones:**
- ADMIN

**Resultado:** Permisos del usuario redefinidos dinámicamente.

---

### Caso 4.10: Otorgar permisos especiales

**Actor:** ADMIN  
**Objetivo:** Override permisos de roles individuales

**Pasos A: Otorgar permiso especial a usuario**

1. `/admin/users` → usuario
2. Click **Permissions**
3. Select permiso (MINT, VIEW_TRANSFERS, etc.)
4. Click **Grant**
5. Audit: `ACTION_PERMISSION_GRANTED`
6. Usuario obtiene permiso aunque su rol no lo tenga

**Pasos B: Revocar permiso especial**

1. Click **Revoke** en permiso
2. Audit: `ACTION_PERMISSION_REVOKED`
3. Usuario pierde permiso (vuelve a baseline de rol)

**Precondiciones:**
- ADMIN con permiso MANAGE_PERMISSIONS

**Resultado:** Permisos granulares por usuario.

---

### Caso 4.11: Ver audit log

**Actor:** ADMIN  
**Objetivo:** Revisar todas las acciones administrativas y auditoría

**Pasos:**

1. Navega a `/admin/audit` o `/admin`
2. Tabla de eventos con:
   - Timestamp
   - Actor (quién hizo la acción)
   - Action (qué se hizo)
   - Target (a quién/qué)
   - Details (JSON con parámetros)
3. Filtros:
   - Action (EXCHANGE_RATE_SET, USER_BANNED, etc.)
   - Actor ID
   - Target ID
   - Rango de fechas
4. Sort por timestamp (desc)
5. Paginación: 50-200 resultados por página
6. Export opcionalmente a CSV/JSON

**Precondiciones:**
- ADMIN

**Resultado:** Auditoría completa de todas las acciones.

---

### Caso 4.12: Congelar/descongelar wallet

**Actor:** ADMIN  
**Objetivo:** Bloquear temporalmente una wallet de usuario (sin borrar)

**Pasos A: Congelar wallet**

1. `/admin/wallets`
2. Click wallet
3. Click **Freeze**
4. Confirma
5. Sistema:
   - Marca `frozen=true`
   - Wallet no puede enviar fondos
   - Puede recibir
   - Audit: `ACTION_WALLET_FROZEN`

**Pasos B: Descongelar**

1. Click **Unfreeze**
2. Wallet operativa nuevamente
3. Audit: `ACTION_WALLET_UNFROZEN`

**Precondiciones:**
- ADMIN

**Resultado:** Wallet bloqueada/desbloqueada, fondos íntegros.

---

## 5. PERMISOS POR FLUJO

### VIEWER (Por defecto)
- ✓ Crear wallet propia
- ✓ Ver wallets propias y balances
- ✓ Ver cadena pública
- ✓ Ver mempool
- ✗ Transferir
- ✗ Minar
- ✗ Admin

### OPERATOR
- ✓ Todo de VIEWER
- ✓ Transferir entre usuarios
- ✓ Exchange (monedas)
- ✓ Minar bloques
- ✓ Ver audit
- ✗ Admin (crear currency, treasury, etc.)

### ADMIN
- ✓ Todo de OPERATOR
- ✓ Crear monedas
- ✓ Treasury wallet
- ✓ Mint/top-up
- ✓ Configurar tasas
- ✓ Sincronizar tasas Binance
- ✓ Gestionar usuarios (ban/roles)
- ✓ Ver audit completo

---

## 6. ESCENARIOS COMPLEJOS

### Escenario A: Trading multi-moneda

**Actor:** User (OPERATOR)  
1. Crea wallet BTC, wallet ETH, wallet SOL
2. Recibe 1 BTC desde treasury
3. Exchange 1 BTC → 15.5 ETH (tasa 15.5, fee 1%)
   - Recibe: 15.5 - 0.155 = 15.345 ETH
4. Envía 5 ETH a otro usuario
5. Admin actualiza tasa BTC→SOL desde Binance (ej: 22000)
6. Exchange 10.345 ETH → 0.047 BTC (aproximadamente)
7. Retira 1 BTC a wallet personal (fuera de plataforma)

**Resultado:** Ciclo completo de conversión, transacción, y retiro.

---

### Escenario B: Auditoria de fraude

**Actor:** ADMIN investigando transacción sospechosa  
1. Usuario reporta envío no autorizado
2. ADMIN navega a `/admin/audit`
3. Filtra por Target ID (wallet del usuario)
4. Encuentra `TRANSFER` en timestamp sospechoso
5. Identifica actor (alguien más logueó su cuenta)
6. Click en audit entry → detalles completos:
   - IP origen
   - Timestamp exacto
   - Monto y comisión
   - Wallet destino
7. ADMIN:
   - Banea usuario atacante
   - Descongela wallet víctima (si fue congelada)
   - Top-up compensación desde treasury
   - Documenta en logs
8. Auditoría completa preservada para análisis posterior

---

### Escenario C: Onboarding masivo

**Escenario:** Empresa quiere onboardear 100 empleados con crypto de nómina

**Pasos:**
1. ADMIN crea wallet de tesorería USDT
2. ADMIN ejecuta mint: $500,000 a tesorería
3. ADMIN crea usuarios en batch (CLI o importación)
4. Cada usuario:
   - Recibe username + contraseña temporal
   - Se activa (primer login)
   - Top-up automático: $5,000 USDT
5. 100 usuarios tienen wallet USDT con saldo inicial
6. Pueden:
   - Ver balance
   - Transferir entre ellos
   - Reportes de transacciones
7. ADMIN monitorea en audit log toda la actividad

**Resultado:** Plataforma lista para pago de nómina en crypto.

---

## 7. RECUPERACIÓN DE ERRORES

### Error: Contraseña olvidada

- Usuario no puede recuperar sin email (SMTP deshabilitado en test)
- ADMIN puede:
  1. `/admin/users` → usuario
  2. Click **Issue Temp Password**
  3. ADMIN comparte temp password por canal seguro
  4. Usuario login con temp, debe cambiar immediatamente
  5. Audit: `ACTION_TEMP_PASSWORD_ISSUED`

### Error: Wallet congelada

- Usuario ve "Wallet is frozen" al intentar transferir
- ADMIN debe descongelar `/admin/wallets`
- Usuario reintenta

### Error: Insufficient balance

- Sistema rechaza transferencia
- Usuario debe esperar bloque siguiente (confirmación) o top-up más

### Error: Tasa no configurada

- Exchange falla: "No exchange rate for BTC/ETH"
- ADMIN debe configurar tasa manual O activar scheduler Binance
- Usuario reintenta

---

## 8. SEGURIDAD Y AUDITORÍA

### Principios

- **Todas las acciones se auditan:** cada PUT, POST, DELETE
- **Soft-delete:** usuarios y transacciones nunca se borran
- **Permisos granulares:** cada endpoint requiere permiso específico
- **Rate limiting:** máx 5 bloques/min, throttling general
- **JWT tokens:** 30 min TTL, refresh no implementado (sesiones cortas)
- **Contraseñas:** bcrypt 12 rounds, almacenadas hasheadas
- **RBAC dinámico:** roles y permisos modificables en tiempo real

### Auditoría disponible

Cada acción registra:
- `actor_id`: quién hizo
- `action`: qué se hizo (ACTION_TRANSFER, ACTION_USER_BANNED, etc.)
- `target_id`: sobre quién/qué
- `details`: JSON con parámetros (monto, fee, provider, etc.)
- `created_at`: timestamp exacto

---

## Resumen de Flujos Principales

| Flujo | VIEWER | OPERATOR | ADMIN |
|-------|--------|----------|-------|
| Registro e Ingreso | ✓ | ✓ | ✓ |
| Ver/crear wallets | ✓ | ✓ | ✓ |
| Transferencia | ✗ | ✓ | ✓ |
| Exchange | ✗ | ✓ | ✓ |
| Minar | ✗ | ✓ | ✓ |
| Ver cadena | ✓ | ✓ | ✓ |
| Admin usuarios | ✗ | ✗ | ✓ |
| Monedas/Treasury | ✗ | ✗ | ✓ |
| Tasas (manual) | ✗ | ✗ | ✓ |
| Tasas (Binance) | ✗ | ✗ | ✓ |
| Audit log | ✗ | ✓ | ✓ |

---

**Fin de guía de casos de uso.**
