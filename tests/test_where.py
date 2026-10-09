import pytest
from sqlalchemy.orm import Session

from alchemic import Mapped, Model, mapped_column


class Product(Model):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    price: Mapped[int]


@pytest.fixture
def seeded_products(session: Session):
    session.add_all(
        [
            Product(id=1, name="Apple", price=10),
            Product(id=2, name="Banana", price=20),
            Product(id=3, name="Cherry", price=30),
        ]
    )
    session.commit()
    return session


def test_where_query_helper(seeded_products: Session):
    stmt = Product.where(Product.price > 15)
    results = seeded_products.execute(stmt).scalars().all()
    assert [product.name for product in results] == ["Banana", "Cherry"]


def test_where_query_helper_empty(seeded_products: Session):
    stmt = Product.where()
    results = seeded_products.execute(stmt).scalars().all()
    assert len(results) == 3


def test_where_query_helper_chaining(seeded_products: Session):
    stmt = Product.where(Product.price >= 10).order_by(Product.price.desc()).limit(2)
    results = seeded_products.execute(stmt).scalars().all()
    assert [product.name for product in results] == ["Cherry", "Banana"]
