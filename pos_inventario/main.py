from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from typing import List

app = FastAPI(title="POS & Inventario API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ¡Tu conexión indestructible a Supabase!
DATABASE_URL = "postgresql://postgres.vyukcvvzizubxdlximyy:u62sTgLkiRyEQvz1@aws-1-us-west-2.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Crea la tabla directamente en Supabase si no existe
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            codigo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            stock INTEGER NOT NULL
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

@app.get("/api/productos")
def listar_productos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos")
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
        return {"status": "success", "message": f"Producto {producto.nombre} creado con éxito."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Error al crear producto. Probablemente el código ya existe.")
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
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        conn.commit()
        return {"status": "success", "message": "Producto actualizado correctamente."}
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
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        conn.commit()
        return {"status": "success", "message": "Producto eliminado correctamente."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/ventas")
def procesar_venta(venta: VentaRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for item in venta.items:
            cursor.execute("SELECT stock, nombre FROM productos WHERE codigo = %s", (item.codigo,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"El producto {item.codigo} no existe.")
            
            stock_actual, nombre = row
            if stock_actual < item.cantidad:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {nombre}.")
        
        for item in venta.items:
            cursor.execute("UPDATE productos SET stock = stock - %s WHERE codigo = %s", (item.cantidad, item.codigo))
        
        conn.commit()
        return {"status": "success", "message": "Venta procesada con éxito."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()