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
    cursor.execute("CREATE TABLE IF NOT EXISTS tiendas (id SERIAL PRIMARY KEY, nombre TEXT DEFAULT 'Mi Tienda', direccion TEXT DEFAULT 'Ciudad', nit TEXT DEFAULT 'C/F', telefono TEXT DEFAULT '---', mensaje_ticket TEXT DEFAULT '¡Gracias por su compra!')")
    cursor.execute("ALTER TABLE tiendas ADD COLUMN IF NOT EXISTS estado TEXT DEFAULT 'activo'")
    cursor.execute("SELECT COUNT(*) FROM tiendas")
    if cursor.fetchone()[0] == 0: cursor.execute("INSERT INTO tiendas (nombre) VALUES ('Tienda Principal')")
    
    cursor.execute("CREATE TABLE IF NOT EXISTS productos (codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL, precio REAL NOT NULL, stock INTEGER NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS ventas (id SERIAL PRIMARY KEY, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total REAL NOT NULL, articulos TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, password TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS auditoria_usuarios (id SERIAL PRIMARY KEY, usuario_modificado TEXT NOT NULL, detalle TEXT NOT NULL, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS caja_sesiones (id SERIAL PRIMARY KEY, cajero TEXT NOT NULL, fondo_inicial REAL NOT NULL, fecha_apertura TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fecha_cierre TIMESTAMP, estado TEXT DEFAULT 'abierta')")
    
    cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol TEXT DEFAULT 'cajero'")
    cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS nombre_completo TEXT")
    cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS password_cambiada BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS cajero TEXT DEFAULT 'Desconocido'")
    
    for tabla in ["usuarios", "productos", "ventas", "auditoria_usuarios", "caja_sesiones"]:
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS tienda_id INTEGER DEFAULT 1")
    
    cursor.execute("UPDATE usuarios SET nombre_completo = usuario WHERE nombre_completo IS NULL")
    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE usuario = 'admin'")
    if cursor.fetchone()[0] == 0: cursor.execute("INSERT INTO usuarios (usuario, password, rol, nombre_completo, tienda_id) VALUES ('admin', '1234', 'superadmin', 'Creador del Sistema', 1)")
    else: cursor.execute("UPDATE usuarios SET rol = 'superadmin' WHERE usuario = 'admin'")
    conn.commit(); conn.close()

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
        return {"usuario": payload["sub"], "rol": payload.get("rol", "cajero"), "nombre_completo": payload.get("nombre", payload["sub"]), "tienda_id": payload.get("tienda_id", 1)}
    except Exception: raise HTTPException(status_code=401, detail="Tu sesión ha expirado.")

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT u.usuario, u.password, u.rol, u.nombre_completo, u.tienda_id, t.estado FROM usuarios u JOIN tiendas t ON u.tienda_id = t.id WHERE u.usuario = %s AND u.password = %s", (req.usuario, req.password))
    user = cursor.fetchone(); conn.close()
    if user:
        if user[5] != 'activo' and user[2] != 'superadmin': raise HTTPException(status_code=403, detail="🚨 Tu cuenta está suspendida por falta de pago. Por favor comunícate con Soporte Técnico al 4941-1913.")
        token = jwt.encode({"sub": user[0], "rol": user[2], "nombre": user[3], "tienda_id": user[4], "exp": datetime.utcnow() + timedelta(hours=12)}, SECRET_KEY, algorithm=ALGORITHM)
        return {"token": token, "usuario": user[0], "rol": user[2], "nombre_completo": user[3], "tienda_id": user[4]}
    raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

# ---- NUEVAS RUTAS SAAS GLOBALES ----
@app.get("/api/saas/tiendas")
def listar_tiendas(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, estado FROM tiendas ORDER BY id ASC")
    rows = cursor.fetchall(); conn.close()
    return [{"id": r[0], "nombre": r[1], "estado": r[2]} for r in rows]

@app.post("/api/saas/tiendas")
def crear_tienda(req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO tiendas (nombre) VALUES (%s) RETURNING id", (req["nombre"],))
    tienda_id = cursor.fetchone()[0]
    cursor.execute("INSERT INTO usuarios (usuario, password, rol, nombre_completo, tienda_id) VALUES (%s, %s, 'admin', %s, %s)", (req["admin_user"], req["admin_pass"], req["admin_nombre"], tienda_id))
    conn.commit(); conn.close()
    return {"status": "success"}

@app.put("/api/saas/tiendas/{id_tienda}/estado")
def toggle_estado_tienda(id_tienda: int, req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE tiendas SET estado = %s WHERE id = %s", (req["estado"], id_tienda))
    conn.commit(); conn.close()
    return {"status": "success"}

# NUEVO: Ruta para borrar cliente por completo
@app.delete("/api/saas/tiendas/{id_tienda}")
def eliminar_tienda_saas(id_tienda: int, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    if id_tienda == 1: raise HTTPException(status_code=400, detail="No puedes borrar tu propia tienda maestra.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        # Borrar en cascada manual
        cursor.execute("DELETE FROM ventas WHERE tienda_id = %s", (id_tienda,))
        cursor.execute("DELETE FROM caja_sesiones WHERE tienda_id = %s", (id_tienda,))
        cursor.execute("DELETE FROM auditoria_usuarios WHERE tienda_id = %s", (id_tienda,))
        cursor.execute("DELETE FROM productos WHERE tienda_id = %s", (id_tienda,))
        cursor.execute("DELETE FROM usuarios WHERE tienda_id = %s", (id_tienda,))
        cursor.execute("DELETE FROM tiendas WHERE id = %s", (id_tienda,))
        conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: conn.close()

# NUEVO: Generador de token de Visita (Impersonation)
@app.post("/api/saas/impersonate/{id_tienda}")
def visitar_tienda(id_tienda: int, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT nombre FROM tiendas WHERE id = %s", (id_tienda,))
    tienda = cursor.fetchone()
    conn.close()
    if not tienda: raise HTTPException(status_code=404, detail="La tienda no existe.")
    
    # Creamos un Token falso pero válido a tu nombre para esa tienda
    token = jwt.encode({"sub": "dios_creador", "rol": "superadmin", "nombre": "Soporte Central", "tienda_id": id_tienda, "exp": datetime.utcnow() + timedelta(hours=4)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"token": token, "tienda_id": id_tienda}

# DIRECTORIO GLOBAL DE USUARIOS (SaaS)
@app.get("/api/saas/usuarios")
def saas_listar_usuarios(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT u.id, u.usuario, u.password, u.rol, u.nombre_completo, t.nombre, u.tienda_id FROM usuarios u JOIN tiendas t ON u.tienda_id = t.id ORDER BY t.id ASC, u.id ASC")
    rows = cursor.fetchall(); conn.close()
    return [{"id": r[0], "usuario": r[1], "password": r[2], "rol": r[3], "nombre_completo": r[4], "tienda_nombre": r[5], "tienda_id": r[6]} for r in rows]

@app.put("/api/saas/usuarios/{id_usuario}")
def saas_editar_usuario(id_usuario: int, req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    if id_usuario == 1 and req.get("rol") != "superadmin": raise HTTPException(status_code=400, detail="No puedes quitarte tu propio rol.")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET usuario=%s, password=%s, rol=%s, nombre_completo=%s, password_cambiada=FALSE WHERE id=%s", (req["usuario"], req["password"], req["rol"], req["nombre_completo"], id_usuario))
    conn.commit(); conn.close()
    return {"status": "success"}

@app.delete("/api/saas/usuarios/{id_usuario}")
def saas_eliminar_usuario(id_usuario: int, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] != "superadmin" or user_info["tienda_id"] != 1: raise HTTPException(status_code=403, detail="Denegado")
    if id_usuario == 1: raise HTTPException(status_code=400, detail="No puedes borrar a tu propio usuario maestro.")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM usuarios WHERE id = %s", (id_usuario,))
    conn.commit(); conn.close()
    return {"status": "success"}

# ---- RUTAS NORMALES ----
@app.get("/api/ajustes")
def obtener_ajustes(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT nombre, direccion, nit, telefono, mensaje_ticket FROM tiendas WHERE id = %s", (user_info["tienda_id"],)); row = cursor.fetchone(); conn.close()
    if not row: return {"nombre": "Mi Tienda", "direccion": "", "nit": "", "telefono": "", "footer": "¡Gracias por su compra!"}
    return {"nombre": row[0], "direccion": row[1], "nit": row[2], "telefono": row[3], "footer": row[4]}

@app.put("/api/ajustes")
def guardar_ajustes(req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("UPDATE tiendas SET nombre=%s, direccion=%s, nit=%s, telefono=%s, mensaje_ticket=%s WHERE id=%s", (req.get("nombre"), req.get("direccion"), req.get("nit"), req.get("telefono"), req.get("footer"), user_info["tienda_id"])); conn.commit(); conn.close()
    return {"status": "success"}

@app.get("/api/caja/estado")
def estado_caja(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT id, fondo_inicial FROM caja_sesiones WHERE cajero = %s AND estado = 'abierta' AND tienda_id = %s", (user_info["nombre_completo"], user_info["tienda_id"])); row = cursor.fetchone(); conn.close()
    if row: return {"abierta": True, "id": row[0], "fondo_inicial": row[1]}
    return {"abierta": False}

@app.post("/api/caja/abrir")
def abrir_caja(req: CajaAbrir, user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT id FROM caja_sesiones WHERE cajero = %s AND estado = 'abierta' AND tienda_id = %s", (user_info["nombre_completo"], user_info["tienda_id"]))
    if cursor.fetchone(): conn.close(); raise HTTPException(status_code=400, detail="Ya tienes un turno abierto.")
    cursor.execute("INSERT INTO caja_sesiones (cajero, fondo_inicial, tienda_id) VALUES (%s, %s, %s)", (user_info["nombre_completo"], req.fondo_inicial, user_info["tienda_id"])); conn.commit(); conn.close()
    return {"status": "success"}

@app.post("/api/caja/cerrar")
def cerrar_caja(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT id, fondo_inicial, fecha_apertura FROM caja_sesiones WHERE cajero = %s AND estado = 'abierta' AND tienda_id = %s", (user_info["nombre_completo"], user_info["tienda_id"])); row = cursor.fetchone()
    if not row: conn.close(); raise HTTPException(status_code=400, detail="No tienes un turno abierto para cerrar.")
    caja_id, fondo_inicial, fecha_apertura = row
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM ventas WHERE cajero = %s AND fecha >= %s AND tienda_id = %s", (user_info["nombre_completo"], fecha_apertura, user_info["tienda_id"])); total_ventas = cursor.fetchone()[0]
    cursor.execute("UPDATE caja_sesiones SET estado = 'cerrada', fecha_cierre = CURRENT_TIMESTAMP WHERE id = %s", (caja_id,)); conn.commit(); conn.close()
    return {"fondo_inicial": fondo_inicial, "total_ventas": total_ventas, "total_esperado": fondo_inicial + total_ventas, "fecha_apertura": fecha_apertura.strftime("%d/%m/%Y %H:%M")}

@app.get("/api/caja/historial")
def historial_cajas(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT c.id, c.cajero, c.fondo_inicial, c.fecha_apertura, c.fecha_cierre, c.estado, COALESCE((SELECT SUM(total) FROM ventas v WHERE v.cajero = c.cajero AND v.fecha >= c.fecha_apertura AND (c.fecha_cierre IS NULL OR v.fecha <= c.fecha_cierre) AND v.tienda_id = c.tienda_id), 0) as total_ventas FROM caja_sesiones c WHERE c.tienda_id = %s ORDER BY c.fecha_apertura DESC LIMIT 100", (user_info["tienda_id"],)); rows = cursor.fetchall(); conn.close()
    return [{"id": r[0], "cajero": r[1], "fondo_inicial": r[2], "fecha_apertura": r[3].strftime("%d/%m/%Y %H:%M") if r[3] else "---", "fecha_cierre": r[4].strftime("%d/%m/%Y %H:%M") if r[4] else "Turno en curso...", "estado": r[5], "total_ventas": r[6], "total_esperado": r[2] + r[6]} for r in rows]

@app.get("/api/usuarios")
def listar_usuarios(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Acceso denegado.")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, usuario, password, rol, nombre_completo, password_cambiada FROM usuarios WHERE tienda_id = %s ORDER BY id ASC", (user_info["tienda_id"],))
    rows = cursor.fetchall(); conn.close()
    return [{"id": r[0], "usuario": r[1], "password": (r[2] if user_info["rol"] == "superadmin" else "********"), "rol": r[3], "nombre_completo": r[4], "password_cambiada": r[5]} for r in rows]

@app.post("/api/usuarios")
def crear_usuario(req: UsuarioRequest, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    if bool(re.search(r"\s", req.usuario)): raise HTTPException(status_code=400, detail="Sin espacios en el Login.")
    if user_info["rol"] == "admin" and req.rol != "cajero": raise HTTPException(status_code=403, detail="Solo el Súper Admin puede asignar nuevos Administradores.")
    conn = get_db_connection(); cursor = conn.cursor()
    try: cursor.execute("INSERT INTO usuarios (usuario, password, rol, nombre_completo, tienda_id) VALUES (%s, %s, %s, %s, %s)", (req.usuario, req.password, req.rol, req.nombre_completo, user_info["tienda_id"])); conn.commit(); return {"status": "success"}
    except Exception: conn.rollback(); raise HTTPException(status_code=400, detail="Usuario en uso."); 
    finally: conn.close()

@app.put("/api/usuarios/{id_usuario}")
def editar_usuario(id_usuario: int, req: dict, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT usuario, password, rol, nombre_completo, password_cambiada FROM usuarios WHERE id = %s AND tienda_id = %s", (id_usuario, user_info["tienda_id"]))
        target = cursor.fetchone()
        if not target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        old_usuario, old_password, old_rol, old_nombre, old_cambiada = target
        
        if old_rol == "superadmin" and user_info["rol"] != "superadmin": raise HTTPException(status_code=403, detail="No puedes modificar la cuenta del Creador.")
        new_usuario = req.get("usuario", old_usuario); new_password = req.get("password", old_password); new_rol = req.get("rol", old_rol); new_nombre = req.get("nombre_completo", old_nombre)
        
        if id_usuario == 1 and new_rol != "superadmin": new_rol = "superadmin"
        if user_info["rol"] == "admin" and (new_usuario != old_usuario or new_rol != old_rol): raise HTTPException(status_code=403, detail="Como Admin, solo puedes modificar Nombres y Contraseñas.")
        
        # NUEVO SEGURO: Evitar que se quite el rol de admin si es el último
        if old_rol == "admin" and new_rol != "admin":
            cursor.execute("SELECT COUNT(*) FROM usuarios WHERE tienda_id = %s AND rol = 'admin'", (user_info["tienda_id"],))
            if cursor.fetchone()[0] <= 1:
                raise HTTPException(status_code=400, detail="Operación denegada. La tienda debe conservar al menos 1 Administrador.")

        nuevo_cambiada = old_cambiada
        if old_password != new_password:
            if user_info["rol"] == "admin":
                if old_cambiada: raise HTTPException(status_code=403, detail="La contraseña ya fue cambiada 1 vez. Contacta a Soporte para reiniciarla.")
                nuevo_cambiada = True
            elif user_info["rol"] == "superadmin": nuevo_cambiada = False
                
        cursor.execute("UPDATE usuarios SET usuario=%s, password=%s, rol=%s, nombre_completo=%s, password_cambiada=%s WHERE id=%s AND tienda_id=%s", (new_usuario, new_password, new_rol, new_nombre, nuevo_cambiada, id_usuario, user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(status_code=400, detail=str(e))
    finally: conn.close()

@app.delete("/api/usuarios/{id_usuario}")
def eliminar_usuario(id_usuario: int, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT rol, usuario FROM usuarios WHERE id = %s AND tienda_id = %s", (id_usuario, user_info["tienda_id"]))
        rol_target = cursor.fetchone()
        if not rol_target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        if rol_target[0] == "superadmin":
            if rol_target[1] == 'admin': raise HTTPException(status_code=403, detail="El Creador principal no se puede borrar.")
            elif user_info["rol"] != "superadmin": raise HTTPException(status_code=403, detail="Intocable.")
        
        # NUEVO SEGURO: Evitar borrar al último Administrador
        if rol_target[0] == "admin":
            cursor.execute("SELECT COUNT(*) FROM usuarios WHERE tienda_id = %s AND rol = 'admin'", (user_info["tienda_id"],))
            if cursor.fetchone()[0] <= 1:
                raise HTTPException(status_code=400, detail="Operación denegada. No puedes eliminar al último Administrador de la tienda.")

        cursor.execute("DELETE FROM usuarios WHERE id = %s AND tienda_id = %s", (id_usuario, user_info["tienda_id"])); conn.commit(); return {"status": "success"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: conn.close()

@app.get("/api/auditoria")
def obtener_auditoria(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] not in ["superadmin", "admin"]: raise HTTPException(status_code=403, detail="Sin permisos.")
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT fecha, usuario_modificado, detalle FROM auditoria_usuarios WHERE tienda_id = %s ORDER BY fecha DESC LIMIT 50", (user_info["tienda_id"],)); rows = cursor.fetchall(); conn.close()
    return [{"fecha": row[0].strftime("%d/%m/%Y %H:%M"), "usuario": row[1], "detalle": row[2]} for row in rows]

@app.get("/api/productos")
def listar_productos(user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT codigo, nombre, precio, stock FROM productos WHERE tienda_id = %s ORDER BY nombre ASC", (user_info["tienda_id"],)); rows = cursor.fetchall(); conn.close()
    return [{"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]} for row in rows]

@app.get("/api/productos/{codigo}")
def obtener_producto(codigo: str, user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT codigo, nombre, precio, stock FROM productos WHERE codigo = %s AND tienda_id = %s", (codigo, user_info["tienda_id"])); row = cursor.fetchone(); conn.close()
    if not row: raise HTTPException(status_code=404, detail="Producto no registrado."); return {"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]}

@app.post("/api/productos")
def crear_producto(producto: Producto, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Denegado."); 
    if producto.precio < 0: raise HTTPException(status_code=400, detail="Precio no puede ser negativo.")
    conn = get_db_connection(); cursor = conn.cursor()
    try: cursor.execute("INSERT INTO productos (codigo, nombre, precio, stock, tienda_id) VALUES (%s, %s, %s, %s, %s)", (producto.codigo, producto.nombre, producto.precio, producto.stock, user_info["tienda_id"])); conn.commit(); return {"status": "success"}
    except Exception: conn.rollback(); raise HTTPException(status_code=400, detail="Código duplicado."); 
    finally: conn.close()

@app.put("/api/productos/{codigo}")
def actualizar_producto(codigo: str, producto: Producto, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Denegado."); 
    if producto.precio < 0: raise HTTPException(status_code=400, detail="Precio no puede ser negativo.")
    conn = get_db_connection(); cursor = conn.cursor()
    try: cursor.execute("UPDATE productos SET codigo = %s, nombre = %s, precio = %s, stock = %s WHERE codigo = %s AND tienda_id = %s", (producto.codigo, producto.nombre, producto.precio, producto.stock, codigo, user_info["tienda_id"])); conn.commit(); return {"status": "success"}
    except Exception: conn.rollback(); raise HTTPException(status_code=500, detail="Error."); 
    finally: conn.close()

@app.delete("/api/productos/{codigo}")
def eliminar_producto(codigo: str, user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Denegado."); 
    conn = get_db_connection(); cursor = conn.cursor()
    try: cursor.execute("DELETE FROM productos WHERE codigo = %s AND tienda_id = %s", (codigo, user_info["tienda_id"])); conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e)); 
    finally: conn.close()

@app.post("/api/ventas")
def procesar_venta(venta: VentaRequest, user_info: dict = Depends(verificar_token)):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        total_venta = 0; detalles_lista = []
        for item in venta.items:
            cursor.execute("SELECT stock, nombre, precio FROM productos WHERE codigo = %s AND tienda_id = %s", (item.codigo, user_info["tienda_id"])); row = cursor.fetchone()
            if not row: raise HTTPException(status_code=404, detail=f"No existe {item.codigo}"); 
            if row[0] < item.cantidad: raise HTTPException(status_code=400, detail=f"Sin stock para {row[1]}")
            total_venta += row[2] * item.cantidad; detalles_lista.append(f"{item.cantidad}x {row[1]}")
        for item in venta.items: cursor.execute("UPDATE productos SET stock = stock - %s WHERE codigo = %s AND tienda_id = %s", (item.cantidad, item.codigo, user_info["tienda_id"]))
        detalles_str = " | ".join(detalles_lista)
        cursor.execute("INSERT INTO ventas (total, articulos, cajero, tienda_id) VALUES (%s, %s, %s, %s)", (total_venta, detalles_str, user_info["nombre_completo"], user_info["tienda_id"]))
        conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally: conn.close()

@app.get("/api/ventas/historial")
def historial_ventas(user_info: dict = Depends(verificar_token)):
    if user_info["rol"] == "cajero": raise HTTPException(status_code=403, detail="Denegado.")
    conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("SELECT id, fecha, total, articulos, cajero FROM ventas WHERE tienda_id = %s ORDER BY fecha DESC LIMIT 100", (user_info["tienda_id"],)); rows = cursor.fetchall(); conn.close()
    return [{"id": row[0], "fecha": row[1].strftime("%d/%m/%Y %H:%M"), "total": row[2], "articulos": row[3], "cajero": row[4]} for row in rows]