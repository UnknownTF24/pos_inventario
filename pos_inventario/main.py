from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from typing import List
import jwt
from datetime import datetime, timedelta
import re

app = FastAPI(title="POS & Inventario API (Multi-Tienda)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = "postgresql://postgres.vyukcvvzizubxdlximyy:u62sTgLkiRyEQvz1@aws-1-us-west-2.pooler.supabase.com:6543/postgres"
SECRET_KEY = "mi_clave_super_secreta_y_larga_cambiala_luego"
ALGORITHM = "HS256"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Tabla de Tiendas (Tenants)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tiendas (
            id SERIAL PRIMARY KEY,
            nombre TEXT DEFAULT 'Mi Tienda',
            direccion TEXT DEFAULT 'Ciudad',
            nit TEXT DEFAULT 'C/F',
            telefono TEXT DEFAULT '---',
            mensaje_ticket TEXT DEFAULT '¡Gracias por su compra!'
        )
    """)
    # Asegurar que exista al menos la Tienda Principal (ID 1)
    cursor.execute("SELECT COUNT(*) FROM tiendas")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO tiendas (nombre) VALUES ('Tienda Principal')")

    # 2. Creación de tablas base
    cursor.execute("""CREATE TABLE IF NOT EXISTS productos (codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL, precio REAL NOT NULL, stock INTEGER NOT NULL)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS ventas (id SERIAL PRIMARY KEY, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total REAL NOT NULL, articulos TEXT NOT NULL)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, password TEXT NOT NULL)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS auditoria_usuarios (id SERIAL PRIMARY KEY, usuario_modificado TEXT NOT NULL, detalle TEXT NOT NULL, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS caja_sesiones (id SERIAL PRIMARY KEY, cajero TEXT NOT NULL, fondo_inicial REAL NOT NULL, fecha_apertura TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fecha_cierre TIMESTAMP, estado TEXT DEFAULT 'abierta')""")
    
    # 3. Alteraciones para columnas faltantes
    cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol TEXT DEFAULT 'cajero'")
    cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS nombre_completo TEXT")
    cursor.execute("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS cajero TEXT DEFAULT 'Desconocido'")
    
    # 4. AISLAMIENTO MULTI-TIENDA: Agregar tienda_id a TODAS las tablas
    for tabla in ["usuarios", "productos", "ventas", "auditoria_usuarios", "caja_sesiones"]:
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS tienda_id INTEGER DEFAULT 1")
    
    # Reparar nulos antiguos
    cursor.execute("UPDATE usuarios SET nombre_completo = usuario WHERE nombre_completo IS NULL")
    
    # Usuario Creador Maestro (Atado a Tienda 1 por ahora)
    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE usuario = 'admin'")
    if cursor.fetchone()[0] == 0: 
        cursor.execute("INSERT INTO usuarios (usuario, password, rol, nombre_completo, tienda_id) VALUES ('admin', '1234', 'superadmin', 'Creador del Sistema', 1)")
    else: 
        cursor.execute("UPDATE usuarios SET rol = 'superadmin' WHERE usuario = 'admin'")
    
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event(): init_db()

class Producto(BaseModel): codigo: str; nombre: str; precio: float; stock: int
class ItemVenta(BaseModel): codigo: str; cantidad: int
class VentaRequest(BaseModel): items: List[ItemVenta]
class LoginRequest(BaseModel): usuario: str; password: str
class UsuarioRequest(BaseModel): usuario: str; password: str; rol: str; nombre_completo: str
class CajaAbrir(BaseModel): fondo_inicial: float

def verificar_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(status_code=401, detail="No autorizado")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {
            "usuario": payload["sub"], 
            "rol": payload.get("rol", "cajero"), 
            "nombre_completo": payload.get("nombre", payload["sub"]),
            "tienda_id": payload.get("tienda_id", 1)
        }
    except Exception: raise HTTPException(status_code=401, detail="Tu sesión ha expirado.")

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT usuario, password, rol, nombre_completo, tienda_id FROM usuarios WHERE usuario = %s AND password = %s", (req.usuario, req.password))
    user = cursor.fetchone()
    conn.close()
    if user:
        token = jwt.encode({"sub": user[0], "rol": user[2], "nombre": user[3], "tienda_id": user[4], "exp": datetime.utcnow() + timedelta(hours=12)}, SECRET_KEY, algorithm=ALGORITHM)
        return {"token": token, "usuario": user[0], "rol": user[2], "nombre_completo": user[3], "tienda_id": user[4]}
    raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

# ---- RUTAS DE IDENTIDAD (AJUSTES) EN LA NUBE ----
@app.get("/api/ajustes")
def obtener_ajustes(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nombre, direccion, nit, telefono, mensaje_ticket FROM tiendas WHERE id = %s", (user_info["tienda_id"],))
    row = cursor.fetchone()
    conn.close()
    if not row: return {"nombre": "Mi Tienda", "direccion": "", "nit": "", "telefono": "", "footer": "¡Gracias por su compra!"}
    return {"nombre": row[0], "direccion": row[1], "nit": row[2], "telefono": row[3], "footer": row[4]}

@app.put("/api/ajustes")
def guardar_ajustes(req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tiendas SET nombre=%s, direccion=%s, nit=%s, telefono=%s, mensaje_ticket=%s WHERE id=%s",
                   (req.get("nombre"), req.get("direccion"), req.get("nit"), req.get("telefono"), req.get("footer"), user_info["tienda_id"]))
    conn.commit()
    conn.close()
    return {"status": "success"}

# ---- RUTAS DE CAJA (Aisladas por tienda) ----
@app.get("/api/caja/estado")
def estado_caja(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, fondo_inicial FROM caja_sesiones WHERE cajero = %s AND estado = 'abierta' AND tienda_id = %s", (user_info["nombre_completo"], user_info["tienda_id"]))
    row = cursor.fetchone()
    conn.close()
    if row: return {"abierta": True, "id": row[0], "fondo_inicial": row[1]}
    return {"abierta": False}

@app.post("/api/caja/abrir")
def abrir_caja(req: CajaAbrir, user_info: dict = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM caja_sesiones WHERE cajero = %s AND estado = 'abierta' AND tienda_id = %s", (user_info["nombre_completo"], user_info["tienda_id"]))
    if cursor.fetchone():
        conn.close(); raise HTTPException(status_code=400, detail="Ya tienes un turno abierto.")
    cursor.execute("INSERT INTO caja_sesiones (cajero, fondo_inicial, tienda_id) VALUES (%s, %s, %s)", (user_info["nombre_completo"], req.fondo_inicial, user_info["tienda_id"]))
    conn.commit(); conn.close()
    return {"status": "success"}

@app.post("/api/caja/cerrar")
def cerrar_caja(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, fondo_inicial, fecha_apertura FROM caja_sesiones WHERE cajero = %s AND estado = 'abierta' AND tienda_id = %s", (user_info["nombre_completo"], user_info["tienda_id"]))
    row = cursor.fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=400, detail="No tienes un turno abierto para cerrar.")
    
    caja_id, fondo_inicial, fecha_apertura = row
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM ventas WHERE cajero = %s AND fecha >= %s AND tienda_id = %s", (user_info["nombre_completo"], fecha_apertura, user_info["tienda_id"]))
    total_ventas = cursor.fetchone()[0]
    
    cursor.execute("UPDATE caja_sesiones SET estado = 'cerrada', fecha_cierre = CURRENT_TIMESTAMP WHERE id = %s", (caja_id,))
    conn.commit(); conn.close()
    
    return {"fondo_inicial": fondo_inicial, "total_ventas": total_ventas, "total_esperado": fondo_inicial + total_ventas, "fecha_apertura": fecha_apertura.strftime("%d/%m/%Y %H:%M")}

@app.get("/api/caja/historial")
def historial_cajas(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.cajero, c.fondo_inicial, c.fecha_apertura, c.fecha_cierre, c.estado,
        COALESCE((SELECT SUM(total) FROM ventas v WHERE v.cajero = c.cajero AND v.fecha >= c.fecha_apertura AND (c.fecha_cierre IS NULL OR v.fecha <= c.fecha_cierre) AND v.tienda_id = c.tienda_id), 0) as total_ventas
        FROM caja_sesiones c WHERE c.tienda_id = %s ORDER BY c.fecha_apertura DESC LIMIT 100
    """, (user_info["tienda_id"],))
    rows = cursor.fetchall(); conn.close()
    return [{"id": r[0], "cajero": r[1], "fondo_inicial": r[2], "fecha_apertura": r[3].strftime("%d/%m/%Y %H:%M") if r[3] else "---", "fecha_cierre": r[4].strftime("%d/%m/%Y %H:%M") if r[4] else "Turno en curso...", "estado": r[5], "total_ventas": r[6], "total_esperado": r[2] + r[6]} for r in rows]

# ---- RUTAS AISLADAS DE USUARIOS ----
@app.get("/api/usuarios")
def listar_usuarios(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, usuario, password, rol, nombre_completo FROM usuarios WHERE tienda_id = %s ORDER BY id ASC", (user_info["tienda_id"],))
    rows = cursor.fetchall(); conn.close()
    return [{"id": r[0], "usuario": r[1], "password": (r[2] if user_info["rol"] == "superadmin" else "********"), "rol": r[3], "nombre_completo": r[4]} for r in rows]

@app.post("/api/usuarios")
def crear_usuario(req: UsuarioRequest, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos para crear cuentas.")
    if bool(re.search(r"\s", req.usuario)): raise HTTPException(status_code=400, detail="El nombre de 'Usuario' no puede tener espacios.")
    if user_info["rol"] == "admin" and req.rol != "cajero": raise HTTPException(status_code=403, detail="Contacta al Creador del sistema para asignar Admins.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO usuarios (usuario, password, rol, nombre_completo, tienda_id) VALUES (%s, %s, %s, %s, %s)", (req.usuario, req.password, req.rol, req.nombre_completo, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception:
        conn.rollback(); raise HTTPException(status_code=400, detail="Usuario ya en uso.")
    finally: conn.close()

@app.put("/api/usuarios/{id_usuario}")
def editar_usuario(id_usuario: int, req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT usuario, password, rol, nombre_completo FROM usuarios WHERE id = %s AND tienda_id = %s", (id_usuario, user_info["tienda_id"]))
        target = cursor.fetchone()
        if not target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        old_usuario, old_password, old_rol, old_nombre = target
        if old_rol == "superadmin": raise HTTPException(status_code=403, detail="El Súper Administrador no puede ser modificado desde aquí.")
        new_usuario, new_password, new_rol, new_nombre = req.get("usuario", old_usuario), req.get("password", old_password), req.get("rol", old_rol), req.get("nombre_completo", old_nombre)
        if user_info["rol"] == "admin" and (new_usuario != old_usuario or new_password != old_password or new_rol != old_rol): raise HTTPException(status_code=403, detail="Solo puedes modificar el Nombre en Pantalla.")
        if old_nombre != new_nombre:
            cursor.execute("INSERT INTO auditoria_usuarios (usuario_modificado, detalle, tienda_id) VALUES (%s, %s, %s)", (old_usuario, f"Cambio de nombre: '{old_nombre}' a '{new_nombre}'", user_info["tienda_id"]))
        cursor.execute("UPDATE usuarios SET usuario=%s, password=%s, rol=%s, nombre_completo=%s WHERE id=%s AND tienda_id=%s", (new_usuario, new_password, new_rol, new_nombre, id_usuario, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except HTTPException: raise
    except Exception: conn.rollback(); raise HTTPException(status_code=400, detail="Error al actualizar. ¿Usuario duplicado?")
    finally: conn.close()

@app.delete("/api/usuarios/{id_usuario}")
def eliminar_usuario(id_usuario: int, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT rol FROM usuarios WHERE id = %s AND tienda_id = %s", (id_usuario, user_info["tienda_id"]))
        rol_target = cursor.fetchone()
        if rol_target and rol_target[0] == "superadmin": raise HTTPException(status_code=403, detail="No puedes eliminar al Súper Administrador.")
        cursor.execute("DELETE FROM usuarios WHERE id = %s AND tienda_id = %s", (id_usuario, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: conn.close()

@app.get("/api/auditoria")
def obtener_auditoria(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT fecha, usuario_modificado, detalle FROM auditoria_usuarios WHERE tienda_id = %s ORDER BY fecha DESC LIMIT 50", (user_info["tienda_id"],))
    rows = cursor.fetchall(); conn.close()
    return [{"fecha": row[0].strftime("%d/%m/%Y %H:%M"), "usuario": row[1], "detalle": row[2]} for row in rows]

# ---- INVENTARIO AISLADO ----
@app.get("/api/productos")
def listar_productos(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos WHERE tienda_id = %s ORDER BY nombre ASC", (user_info["tienda_id"],))
    rows = cursor.fetchall(); conn.close()
    return [{"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]} for row in rows]

@app.get("/api/productos/{codigo}")
def obtener_producto(codigo: str, user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos WHERE codigo = %s AND tienda_id = %s", (codigo, user_info["tienda_id"]))
    row = cursor.fetchone(); conn.close()
    if not row: raise HTTPException(status_code=404, detail="Producto no registrado.")
    return {"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]}

@app.post("/api/productos")
def crear_producto(producto: Producto, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Los cajeros no editan inventario.")
    if producto.precio < 0: raise HTTPException(status_code=400, detail="Precio no puede ser negativo.")
    if len(producto.nombre) > 80: raise HTTPException(status_code=400, detail="Nombre demasiado largo.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO productos (codigo, nombre, precio, stock, tienda_id) VALUES (%s, %s, %s, %s, %s)", (producto.codigo, producto.nombre, producto.precio, producto.stock, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception: conn.rollback(); raise HTTPException(status_code=400, detail="El código ya existe en esta tienda.")
    finally: conn.close()

@app.put("/api/productos/{codigo}")
def actualizar_producto(codigo: str, producto: Producto, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Los cajeros no editan inventario.")
    if producto.precio < 0: raise HTTPException(status_code=400, detail="Precio no puede ser negativo.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("UPDATE productos SET codigo = %s, nombre = %s, precio = %s, stock = %s WHERE codigo = %s AND tienda_id = %s", (producto.codigo, producto.nombre, producto.precio, producto.stock, codigo, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception: conn.rollback(); raise HTTPException(status_code=500, detail="Error al actualizar.")
    finally: conn.close()

@app.delete("/api/productos/{codigo}")
def eliminar_producto(codigo: str, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Los cajeros no pueden eliminar productos.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM productos WHERE codigo = %s AND tienda_id = %s", (codigo, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: conn.close()

# ---- VENTAS AISLADAS ----
@app.post("/api/ventas")
def procesar_venta(venta: VentaRequest, user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        total_venta = 0; detalles_lista = []
        for item in venta.items:
            cursor.execute("SELECT stock, nombre, precio FROM productos WHERE codigo = %s AND tienda_id = %s", (item.codigo, user_info["tienda_id"]))
            row = cursor.fetchone()
            if not row: raise HTTPException(status_code=404, detail=f"El producto {item.codigo} no existe.")
            if row[0] < item.cantidad: raise HTTPException(status_code=400, detail=f"Stock insuficiente para {row[1]}.")
            total_venta += row[2] * item.cantidad
            detalles_lista.append(f"{item.cantidad}x {row[1]}")
        for item in venta.items: cursor.execute("UPDATE productos SET stock = stock - %s WHERE codigo = %s AND tienda_id = %s", (item.cantidad, item.codigo, user_info["tienda_id"]))
        detalles_str = " | ".join(detalles_lista)
        cursor.execute("INSERT INTO ventas (total, articulos, cajero, tienda_id) VALUES (%s, %s, %s, %s)", (total_venta, detalles_str, user_info["nombre_completo"], user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: conn.close()

@app.get("/api/ventas/historial")
def historial_ventas(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Los cajeros no tienen acceso a reportes.")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, fecha, total, articulos, cajero FROM ventas WHERE tienda_id = %s ORDER BY fecha DESC LIMIT 100", (user_info["tienda_id"],))
    rows = cursor.fetchall(); conn.close()
    return [{"id": row[0], "fecha": row[1].strftime("%d/%m/%Y %H:%M"), "total": row[2], "articulos": row[3], "cajero": row[4]} for row in rows]