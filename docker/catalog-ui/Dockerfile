FROM node:22-alpine AS build

WORKDIR /src
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG VITE_AUTH_ENABLED=false
ARG VITE_OIDC_AUTHORITY=http://localhost:8180/realms/catalog
ARG VITE_OIDC_CLIENT_ID=catalog-ui
ENV VITE_AUTH_ENABLED=$VITE_AUTH_ENABLED
ENV VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY
ENV VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID
RUN npm run build

FROM nginx:1.29-alpine

COPY docker/catalog-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
