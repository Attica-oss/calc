## Converting a column to an array should be possible.

```py
column("name",1,2,3,5)::ARRAY
```

## Removing header from column when checking type.

Type of a column should not include the column name. Since the column are homogeneous, the type should not include the column name.
Both of these should return `int` instead of `column{name: int}` and `column{test: int}`.

```py
let x = type_of(column("name",1,2,3,5));
let y = type_of(column("test",5,8,9,7));
```

## Implement equality for type.

This code returns `'=' is not defined for type and type.`.
Also to check if 'type' has been implemented as a type.

````py
type_of(column("test",5,8,9,7)) = type_of(column("name",1,2,3,5))```

e.g.

```py
type_of(column("test",5,8,9,7))
````

returns the type as 'text' instead of 'type'.
For example, `type_of(4)` returns `int` and the type is `type`.

For arrays and matrices, this works as expected.

```py
type_of(array(2,5,25));
type_of(matrix(array(5.,5.6,5.2),array(-5.,5.8,15.2)));
```

returns `array{int}` and `matrix{decimal}` and the type is `type`.

whereas for table the type is `type` and the column name is included in the type (`table{name: decimal, age: duration}`).
e.g.

```py
type_of(table(column("name",1.21,5.25),column("age",5y,15y)))
```

## Adding new line to concat looses type information.

when adding a new line to a string, the type information is lost.

```py
concat("test","\n","c");
```

# Implement formatting for column headers

[ ] Differentiate between column headers and column values
e.g.

```py
table(column("name","Garry","lower"))
```

a better syntax will be:

```py
table(column("name":"Garry","lower"))
```

[ ] Normalise column names and prevent spaces between words.

As it becomes difficult to access columns

```py
let x = table(column("First Name","Garry","lower"));

x::FIRST NAME
```

And also:

```py
let x = table(column("First Name","Garry","lower"));

extend(x,"Test",upper([First Name]))
```

The convention will be snake_case, and when loading data from a file, the column names will be converted to snake_case automatically.
While for saving back to a file, the column names can be modified to another case.

# Overwriting existing columns

If we want to overwrite an existing column, we can reuse the columnname to overwrite the existing column.

```py
let x = table(column("name","Garry","lower"));
extend(x,"name",upper([name]))
```

returns extend() column name 'first_name' already exists in this table.

# Droping columns

We need to implement droping columns using a `drop` function.
where we can either drop by column name or multiple column names.

# Type inference for tables

We need to implement type when creating tables.
for example if we want to create an empty table, we can start with a `table` function and add columns names and assign a type.

# a new is_between() function 

We ignore temporal values as we have hours_between() and days_between() functions.
We need to implement a `is_between` function that works with numeric values.

if x >= 11 and x <= 20:
    return True
else:
    return False

# we need to implement a `fn` keyword that allows defining custom functions

```py
let add = fn(x::int,y::int)->int = x + y;

let add_days = fn(x::date,y::duration)->date = x + y::DAYS;
```

for multiline functions, we can use the `fn` keyword with a block of code.
```py
let add_first_and_last_name = fn(first_name::text,last_name::text)->text = {

let first = capitalize(first_name);
let last = capitalize(last_name);

concat(first," ",last) // no need for a semi-colon
};
```
This is an anonymous function

```py
fn(first_name::text,last_name::text)->text = concat(first_name," ",last_name);
```
