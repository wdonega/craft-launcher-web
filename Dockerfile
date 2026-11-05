# Run `python3 build.py` first: it fills site/ with the launcher and every app's web build.
FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/
EXPOSE 80
