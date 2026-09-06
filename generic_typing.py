from collections.abc import Sequence
from typing import Any 

# what type we input is what type will output

def get_first[T](collection: Sequence[T]) -> T:
    """Return the first element of a sequence."""
    return collection[0]


people: list[str] = ["Alice", "Bob", "Charlie"]
details: set[int] = {1, 2, 3}

print (get_first(people), type(get_first(people)))  # Output: Alice
# print(get_first(details), type(get_first(details)))  # Output: 1

db = {
    1: "Alice",
    2: "Bob",
    3: "Charlie"
}

def get_id[K, V](db: dict[K, V], user_id: K) -> V:
    """Return the value associated with a key in a dictionary."""
    return db[user_id]

class CustomList[T]:
    