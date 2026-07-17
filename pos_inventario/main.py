from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from typing import List
from datetime import datetime

app = FastAPI(title="POS & Inventario API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = "postgresql://postgres.vyukcvvzizubxdlximyy:u62sTgLkiRyEQvz1@aws-1-us-west-2.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Tabla de productos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            codigo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            stock INTEGER NOT NULL
        )
    """)
    # NUEVA: Tabla de historial de ventas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total REAL NOT NULL,
            articulos TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

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

# ---- RUTAS DE PRODUCTOS ----
@app.get("/api/productos")
def listar_productos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos ORDER BY nombre ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]} for row in rows]

@app.post("/api/productos")
def crear_producto(producto: Producto):
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

@app.put("/api/productos/{codigo}")
def actualizar_producto(codigo: str, producto: Producto):
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
def eliminar_producto(codigo: str):
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

# ---- RUTAS DE VENTAS (NUEVO) ----
@app.post("/api/ventas")
def procesar_venta(venta: VentaRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        total_venta = 0
        detalles_lista = []

        # 1. Verificar stock y calcular total
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
        
        # 2. Descontar stock
        for item in venta.items:
            cursor.execute("UPDATE productos SET stock = stock - %s WHERE codigo = %s", (item.cantidad, item.codigo))
        
        # 3. Guardar el reporte de la venta
        detalles_str = " | ".join(detalles_lista)
        cursor.execute("INSERT INTO ventas (total, articulos) VALUES (%s, %s)", (total_venta, detalles_str))
        
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/ventas")
def historial_ventas():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, fecha, total, articulos FROM ventas ORDER BY fecha DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    # Formatear la fecha para que se vea bonita
    return [{"id": row[0], "fecha": row[1].strftime("%d/%m/%Y %H:%M"), "total": row[2], "articulos": row[3]} for row in rows]