from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from typing import List
import jwt
from datetime import datetime, timedelta

app = FastAPI(title="POS & Inventario API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = "postgresql://postgres.vyukcvvzizubxdlximyy:u62sTgLkiRyEQvz1@aws-1-us-west-2.pooler.supabase.com:6543/postgres"

# ---- CONFIGURACIÓN DE SEGURIDAD JWT ----
SECRET_KEY = "mi_clave_super_secreta_y_larga_cambiala_luego"
ALGORITHM = "HS256"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            codigo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            stock INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total REAL NOT NULL,
            articulos TEXT NOT NULL
        )
    """)
    # NUEVA TABLA: Usuarios del sistema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            usuario TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    
    # Crear usuario maestro por defecto si la tabla está vacía
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO usuarios (usuario, password) VALUES ('admin', '1234')")
        
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

# ---- MODELOS ----
class Producto(BaseModel):
    codigo: str
    nombre: str
    precio: float
    stock: int

class ItemVenta(BaseModel):
    codigo: str
    cantidad: int

class VentaRequest(BaseModel):
    items: List[ItemVenta]

class LoginRequest(BaseModel):
    usuario: str
    password: str

class UsuarioRequest(BaseModel):
    usuario: str
    password: str

# ---- FUNCIÓN DE VERIFICACIÓN (CANDADO) ----
def verificar_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No autorizado o token faltante")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Tu sesión ha expirado.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido.")

# ---- RUTA DE LOGIN (Verifica en Base de Datos) ----
@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT usuario, password FROM usuarios WHERE usuario = %s AND password = %s", (req.usuario, req.password))
    user = cursor.fetchone()
    conn.close()

    if user:
        expiracion = datetime.utcnow() + timedelta(hours=12)
        token = jwt.encode({"sub": req.usuario, "exp": expiracion}, SECRET_KEY, algorithm=ALGORITHM)
        return {"token": token, "usuario": req.usuario}
    
    raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

# ---- RUTAS DE USUARIOS (Súper Admin) ----
@app.get("/api/usuarios")
def listar_usuarios(admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, usuario, password FROM usuarios ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "usuario": row[1], "password": row[2]} for row in rows]

@app.post("/api/usuarios")
def crear_usuario(req: UsuarioRequest, admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO usuarios (usuario, password) VALUES (%s, %s)", (req.usuario, req.password))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="El nombre de usuario ya existe.")
    finally:
        conn.close()

@app.delete("/api/usuarios/{id_usuario}")
def eliminar_usuario(id_usuario: int, admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Prevenir que borren al último usuario y se queden sin acceso
        cursor.execute("SELECT COUNT(*) FROM usuarios")
        if cursor.fetchone()[0] <= 1:
            raise HTTPException(status_code=400, detail="No puedes eliminar al último usuario del sistema.")
            
        cursor.execute("DELETE FROM usuarios WHERE id = %s", (id_usuario,))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# ---- RUTAS PÚBLICAS (POS) ----
@app.get("/api/productos")
def listar_productos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos ORDER BY nombre ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]} for row in rows]

@app.get("/api/productos/{codigo}")
def obtener_producto(codigo: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos WHERE codigo = %s", (codigo,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Producto no registrado.")
    return {"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]}

@app.post("/api/ventas")
def procesar_venta(venta: VentaRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        total_venta = 0
        detalles_lista = []
        for item in venta.items:
            cursor.execute("SELECT stock, nombre, precio FROM productos WHERE codigo = %s", (item.codigo,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"El producto {item.codigo} no existe.")
            stock_actual, nombre, precio = row
            if stock_actual < item.cantidad:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {nombre}.")
            total_venta += precio * item.cantidad
            detalles_lista.append(f"{item.cantidad}x {nombre}")
        
        for item in venta.items:
            cursor.execute("UPDATE productos SET stock = stock - %s WHERE codigo = %s", (item.cantidad, item.codigo))
        
        detalles_str = " | ".join(detalles_lista)
        cursor.execute("INSERT INTO ventas (total, articulos) VALUES (%s, %s)", (total_venta, detalles_str))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# ---- RUTAS PROTEGIDAS (Admin) ----
@app.post("/api/productos")
def crear_producto(producto: Producto, admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO productos (codigo, nombre, precio, stock) VALUES (%s, %s, %s, %s)",
            (producto.codigo, producto.nombre, producto.precio, producto.stock)
        )
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="El código ya existe.")
    finally:
        conn.close()

@app.put("/api/productos/{codigo}")
def actualizar_producto(codigo: str, producto: Producto, admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE productos SET nombre = %s, precio = %s, stock = %s WHERE codigo = %s",
            (producto.nombre, producto.precio, producto.stock, codigo)
        )
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.delete("/api/productos/{codigo}")
def eliminar_producto(codigo: str, admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM productos WHERE codigo = %s", (codigo,))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/ventas/historial")
def historial_ventas(admin: str = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, fecha, total, articulos FROM ventas ORDER BY fecha DESC LIMIT 100")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "fecha": row[1].strftime("%d/%m/%Y %H:%M"), "total": row[2], "articulos": row[3]} for row in rows]