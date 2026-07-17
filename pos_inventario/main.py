from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from typing import List

app = FastAPI(title="POS & Inventario API")

# Permitir que el frontend se comunique con el backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "inventario.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            codigo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            stock INTEGER NOT NULL
        )
    """)
    # Insertar un par de productos de prueba si la tabla está vacía
    cursor.execute("SELECT COUNT(*) FROM productos")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO productos VALUES ('74010055', 'Gaseosa Cola 600ml', 6.50, 50)")
        cursor.execute("INSERT INTO productos VALUES ('123456', 'Galletas de Chocolate', 3.00, 20)")
        conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

class ItemVenta(BaseModel):
    codigo: str
    cantidad: int

class VentaRequest(BaseModel):
    
    items: List[ItemVenta]

class Producto(BaseModel):
    codigo: str
    nombre: str
    precio: float
    stock: int

@app.get("/api/productos/{codigo}")
def obtener_producto(codigo: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, precio, stock FROM productos WHERE codigo = ?", (codigo,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Producto no registrado.")
    
    return {"codigo": row[0], "nombre": row[1], "precio": row[2], "stock": row[3]}

@app.post("/api/productos")
def crear_producto(producto: Producto):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO productos (codigo, nombre, precio, stock) VALUES (?, ?, ?, ?)",
            (producto.codigo, producto.nombre, producto.precio, producto.stock)
        )
        conn.commit()
        return {"status": "success", "message": f"Producto {producto.nombre} creado con éxito."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Error al crear producto. Probablemente el código ya existe.")
    finally:
        conn.close()

@app.post("/api/ventas")
def procesar_venta(venta: VentaRequest):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        for item in venta.items:
            cursor.execute("SELECT stock, nombre FROM productos WHERE codigo = ?", (item.codigo,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"El producto {item.codigo} no existe.")
            
            stock_actual, nombre = row
            if stock_actual < item.cantidad:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {nombre}.")
        
        for item in venta.items:
            cursor.execute("UPDATE productos SET stock = stock - ? WHERE codigo = ?", (item.cantidad, item.codigo))
            
        conn.commit()
        return {"status": "success", "message": "Venta procesada con éxito."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()